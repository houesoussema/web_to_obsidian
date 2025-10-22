"""
Obsidian Web Crawler using Playwright
=====================================
A web crawler that uses a browser automation engine (Playwright) to capture
fully rendered web content into Obsidian-formatted Markdown files. It can use
an existing browser profile to handle authenticated sessions.

Author: Jules (AI Software Engineer)
"""

import asyncio
import argparse
import re
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse
from collections import deque

import html2text
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Global state
visited_urls = set()
pages_scraped = 0

def get_cli_args():
    """Setup and parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Obsidian Web Crawler using Playwright.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "start_url",
        help="The initial URL to start crawling from."
    )
    parser.add_argument(
        "vault_path",
        type=Path,
        help="The absolute path to your Obsidian vault."
    )
    parser.add_argument(
        "--obsidian-folder",
        default="99-Clippings/Web Scrapes",
        help="The folder within your vault to save scraped content."
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        help="Path to the browser user data directory for authentication (e.g., for Chrome on Windows: C:/Users/YourUser/AppData/Local/Google/Chrome/User Data)."
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="The maximum number of pages to scrape."
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="The delay in seconds between fetching pages."
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the browser in headless mode (no UI)."
    )
    parser.add_argument(
        "--user-agent",
        default="ObsidianWebCrawler/1.0 (Educational Purpose)",
        help="The User-Agent string to use for requests."
    )
    return parser.parse_args()

def is_same_domain(url, base_url):
    """Check if URL belongs to the same domain as base_url."""
    return urlparse(url).netloc == urlparse(base_url).netloc

def normalize_url(url):
    """Normalize URL by removing fragments and trailing slashes."""
    parsed = urlparse(url)
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return normalized.rstrip('/')

def sanitize_filename(title):
    """Convert title to a safe filename."""
    safe = re.sub(r'[<>:"/\\|?*]', '', title)
    safe = re.sub(r'\s+', '-', safe)
    return safe[:100].strip('-') or "untitled"


def resolve_image_urls(soup, base_url):
    """Find all image tags and convert their src/srcset attributes to absolute URLs."""
    for img in soup.find_all('img'):
        if img.has_attr('src'):
            img['src'] = urljoin(base_url, img['src'])

        if img.has_attr('srcset'):
            srcset_parts = [
                ' '.join([urljoin(base_url, url_part.strip().split()[0])] + url_part.strip().split()[1:])
                for url_part in img['srcset'].split(',') if url_part.strip()
            ]
            img['srcset'] = ', '.join(srcset_parts)


def resolve_all_links(soup, base_url):
    """Find all anchor tags and convert their href attributes to absolute URLs."""
    for a in soup.find_all('a', href=True):
        a['href'] = urljoin(base_url, a['href'])


def extract_main_content_from_html(html):
    """Intelligently extract main content from HTML string."""
    soup = BeautifulSoup(html, 'html.parser')
    for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
        element.decompose()

    unwanted_patterns = ['nav', 'menu', 'sidebar', 'advertisement', 'popup', 'cookie', 'banner', 'social', 'share', 'comment', 'footer']
    for pattern in unwanted_patterns:
        for element in soup.find_all(class_=re.compile(pattern, re.I)):
            element.decompose()
        for element in soup.find_all(id=re.compile(pattern, re.I)):
            element.decompose()

    main_content = soup.find('main') or soup.find('article')
    if not main_content:
        content_patterns = ['content', 'main', 'article', 'post', 'entry']
        for pattern in content_patterns:
            main_content = soup.find(class_=re.compile(pattern, re.I)) or soup.find(id=re.compile(pattern, re.I))
            if main_content:
                break

    return main_content or soup.find('body') or soup

def extract_metadata(page_title, html):
    """Extract metadata from page for frontmatter."""
    soup = BeautifulSoup(html, 'html.parser')
    metadata = {
        'title': page_title,
        'author': '[[Web Clipper]]',
        'published': '',
        'description': ''
    }

    for tag in soup.find_all('meta'):
        if tag.get('name', '').lower() in ['author', 'creator']:
            metadata['author'] = tag.get('content', '').strip() or metadata['author']
        if tag.get('property') == 'article:published_time' or tag.get('name') in ['date', 'publishdate']:
            pub_date = tag.get('content', '').strip()
            if pub_date:
                try:
                    metadata['published'] = datetime.fromisoformat(pub_date.replace('Z', '+00:00')).strftime('%Y-%m-%d')
                except ValueError:
                    metadata['published'] = pub_date[:10]
        if tag.get('name') == 'description' or tag.get('property') == 'og:description':
            desc = tag.get('content', '').strip()
            if desc:
                metadata['description'] = desc

    if not metadata['description']:
        text = extract_main_content_from_html(html).get_text(strip=True)
        metadata['description'] = (text[:200] + '...') if len(text) > 200 else text

    return metadata

def html_to_markdown(html_content):
    """Convert HTML content to Markdown format."""
    h = html2text.HTML2Text()
    h.body_width = 0
    return h.handle(str(html_content)).strip()

async def create_markdown_file(page, output_dir):
    """Create Obsidian-formatted Markdown file from a Playwright page object."""
    global pages_scraped

    url = page.url
    html_content = await page.content()

    metadata = extract_metadata(await page.title(), html_content)
    tags = ['clippings', 'web-scrape']

    main_content_html = extract_main_content_from_html(html_content)
    resolve_image_urls(main_content_html, url)  # Resolve image URLs
    resolve_all_links(main_content_html, url)   # Resolve all other links
    markdown_content = html_to_markdown(main_content_html)

    created_date = datetime.now().strftime('%Y-%m-%d')
    frontmatter = f"""---
title: "{metadata['title'] or 'Untitled'}"
source: {url}
author: {metadata['author']}
published: "{metadata['published']}"
created: {created_date}
description: "{metadata['description'].replace('"', '“')}"
tags:
{chr(10).join(f'  - {tag}' for tag in tags)}
---

"""

    full_content = frontmatter + "## Scraped Content\n\n" + markdown_content

    url_hash = abs(hash(url)) % 10000
    filename = f"{sanitize_filename(metadata['title'])}-{url_hash}.md"
    filepath = output_dir / filename
    filepath.write_text(full_content, encoding='utf-8')

    pages_scraped += 1
    print(f"[{pages_scraped}/{get_cli_args().max_pages}] Saved: {filename}")

async def extract_links(page, start_url):
    """Extract all valid, same-domain links from a page."""
    links = set()
    locators = page.locator("a[href]")
    for i in range(await locators.count()):
        href = await locators.nth(i).get_attribute("href")
        if href:
            absolute_url = urljoin(page.url, href)
            normalized_url = normalize_url(absolute_url)
            if is_same_domain(normalized_url, start_url):
                if not re.search(r'\.(pdf|jpg|png|gif|zip)$', normalized_url, re.I):
                    links.add(normalized_url)
    return links

async def crawl_website(args):
    """Perform a breadth-first crawl using Playwright."""
    global visited_urls, pages_scraped

    queue = deque([args.start_url])

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            args.user_data_dir or "",
            headless=args.headless,
            user_agent=get_cli_args().user_agent,
            accept_downloads=False
        )
        page = await context.new_page()

        while queue and pages_scraped < args.max_pages:
            url = queue.popleft()
            normalized_url = normalize_url(url)
            if normalized_url in visited_urls:
                continue

            try:
                print(f"\nFetching: {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                visited_urls.add(normalized_url)

                await create_markdown_file(page, args.vault_path / args.obsidian_folder)

                if pages_scraped < args.max_pages:
                    new_links = await extract_links(page, args.start_url)
                    unvisited_links = [link for link in new_links if normalize_url(link) not in visited_urls]
                    queue.extend(unvisited_links)
                    print(f"Found {len(unvisited_links)} new links to explore.")

                await asyncio.sleep(args.delay)

            except PlaywrightTimeoutError:
                print(f"Timeout error fetching {url}")
            except Exception as e:
                print(f"Error processing {url}: {e}")

        await context.close()

    print(f"\n{'='*70}\nCrawl complete! Scraped {pages_scraped} pages.")

async def main_async():
    """Asynchronous main function to orchestrate the crawl."""
    args = get_cli_args()
    output_path = args.vault_path / args.obsidian_folder
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"\nObsidian Web Crawler (Playwright Edition)")
    print(f"{'='*70}")
    print(f"  Start URL: {args.start_url}")
    print(f"  Output: {output_path}")
    print(f"  Max pages: {args.max_pages}")
    print(f"  Auth Profile: {'Yes' if args.user_data_dir else 'No'}")
    print(f"{'='*70}\n")

    await crawl_website(args)

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nCrawl interrupted by user.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
