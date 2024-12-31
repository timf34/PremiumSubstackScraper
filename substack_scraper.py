import argparse
import json
import os
import hashlib
import mimetypes
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple
from time import sleep
from urllib.parse import urlparse, unquote

from bs4 import BeautifulSoup
import html2text
import markdown
import requests
from tqdm import tqdm
from xml.etree import ElementTree as ET

from selenium import webdriver
from selenium.webdriver.common.by import By
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.service import Service
from config import EMAIL, PASSWORD

USE_PREMIUM: bool = False  # Set to True if you want to login to Substack and convert paid for posts
BASE_SUBSTACK_URL: str = "https://www.thefitzwilliam.com/"  # Substack you want to convert to markdown
BASE_MD_DIR: str = "substack_md_files"  # Name of the directory we'll save the .md essay files
BASE_HTML_DIR: str = "substack_html_pages"  # Name of the directory we'll save the .html essay files
BASE_IMAGE_DIR: str = "substack_images" # Name of the directory we'll save images to if --images is passed in
HTML_TEMPLATE: str = "author_template.html"  # HTML template to use for the author page
JSON_DATA_DIR: str = "data"
NUM_POSTS_TO_SCRAPE: int = 3  # Set to 0 if you want all posts

def clean_linked_images(md_content: str) -> str:
    """
    Converts markdown linked images to simple image references.
    
    Args:
        md_content: String containing markdown content
        
    Returns:
        String with cleaned markdown where linked images are converted to simple image references
        
    Example:
        >>> md = "[![alt text](/img/test.png)](/img/test.png)"
        >>> clean_linked_images(md)
        '![alt text](/img/test.png)'
    """
    # Pattern matches: [![any text](/path/img.ext)](/path/img.ext)
    pattern = r'\[!\[(.*?)\]\((.*?)\)\]\(.*?\)'
    
    # Replace with: ![text](/path/img.ext)
    cleaned = re.sub(pattern, r'![\1](\2)', md_content)
    
    return cleaned

def count_images_in_markdown(md_content: str) -> int:
    """
    Count number of image references in markdown content.
    
    Args:
        md_content: Markdown content to analyze
        
    Returns:
        Number of unique images found
    """
    # First clean linked images
    cleaned_content = clean_linked_images(md_content)
    
    # Then count remaining image references
    pattern = r'!\[.*?\]\((.*?)\)'
    matches = re.findall(pattern, cleaned_content)
    return len(matches)

def is_post_url(url: str) -> bool:
    return "/p/" in url

def get_publication_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"

def extract_main_part(url: str) -> str:
    parts = urlparse(url).netloc.split('.')  # Parse the URL to get the netloc, and split on '.'
    return parts[1] if parts[0] == 'www' else parts[0]  # Return the main part of the domain, while ignoring 'www' if
    # present

def get_post_slug(url: str) -> str:
    match = re.search(r'/p/([^/]+)', url)
    return match.group(1) if match else 'unknown_post'

def sanitize_filename(url: str) -> str:
    """Create a safe filename from URL or content."""
    # Extract original filename from CDN URL
    if "substackcdn.com" in url:
        # Get the actual image URL after the CDN parameters
        original_url = unquote(url.split("https://")[1])
        filename = original_url.split("/")[-1]
    else:
        filename = url.split("/")[-1]
    
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    
    # If filename is too long or empty, create hash-based name
    if len(filename) > 100 or not filename:
        hash_object = hashlib.md5(url.encode())
        ext = mimetypes.guess_extension(requests.head(url).headers.get('content-type', '')) or '.jpg'
        filename = f"{hash_object.hexdigest()}{ext}"
    
    return filename

def download_image(url: str, save_path: Path, pbar: Optional[tqdm] = None) -> Optional[str]:
    """Download image from URL and save to path."""
    try:
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            if pbar:
                pbar.update(1)
            return str(save_path)
    except Exception as e:
        if pbar:
            pbar.write(f"Error downloading image {url}: {str(e)}")
        else:
            print(f"Error downloading image {url}: {str(e)}")
    return None

def process_markdown_images(md_content: str, author: str, post_slug: str, pbar: Optional[tqdm] = None) -> str:
    """Process markdown content to download images and update references."""
    image_dir = Path(BASE_IMAGE_DIR) / author / post_slug
    
    # First clean up any linked images
    md_content = clean_linked_images(md_content)
    
    def replace_image(match):
        url = match.group(0).strip('()')
        filename = sanitize_filename(url)
        save_path = image_dir / filename
        if not save_path.exists():
            download_image(url, save_path, pbar)
        
        rel_path = os.path.relpath(save_path, Path(BASE_MD_DIR) / author)
        return f"({rel_path})"
    
    pattern = r'\(https://substackcdn\.com/image/fetch/[^\s\)]+\)'
    return re.sub(pattern, replace_image, md_content)

def generate_html_file(author_name: str) -> None:
    """
    Generates a HTML file for the given author.
    """
    if not os.path.exists(BASE_HTML_DIR):
        os.makedirs(BASE_HTML_DIR)

    # Read JSON data
    json_path = os.path.join(JSON_DATA_DIR, f'{author_name}.json')
    with open(json_path, 'r', encoding='utf-8') as file:
        essays_data = json.load(file)

    # Convert JSON data to a JSON string for embedding
    embedded_json_data = json.dumps(essays_data, ensure_ascii=False, indent=4)

    with open(HTML_TEMPLATE, 'r', encoding='utf-8') as file:
        html_template = file.read()

    # Insert the JSON string into the script tag in the HTML template
    html_with_data = html_template.replace('<!-- AUTHOR_NAME -->', author_name).replace(
        '<script type="application/json" id="essaysData"></script>',
        f'<script type="application/json" id="essaysData">{embedded_json_data}</script>'
    )
    html_with_author = html_with_data.replace('author_name', author_name)

    # Write the modified HTML to a new file
    html_output_path = os.path.join(BASE_HTML_DIR, f'{author_name}.html')
    with open(html_output_path, 'w', encoding='utf-8') as file:
        file.write(html_with_author)


class BaseSubstackScraper(ABC):
    def __init__(self, base_substack_url: str, md_save_dir: str, html_save_dir: str, download_images: bool = False):
        self.is_single_post: bool = is_post_url(base_substack_url)
        self.base_substack_url: str = get_publication_url(base_substack_url)
        self.writer_name: str = extract_main_part(self.base_substack_url)
        self.post_slug: Optional[str] = get_post_slug(base_substack_url) if self.is_single_post else None
        
        self.md_save_dir: str = Path(md_save_dir) / self.writer_name
        self.html_save_dir: str = Path(html_save_dir) / self.writer_name
        self.image_dir: str = Path(BASE_IMAGE_DIR) / self.writer_name
        self.download_images: bool = download_images

        for directory in [self.md_save_dir, self.html_save_dir]:
            directory.mkdir(parents=True, exist_ok=True)
            print(f"Created directory {directory}")

        if self.is_single_post:
            self.post_urls = [base_substack_url]
        else:
            self.keywords: List[str] = ["about", "archive", "podcast"]
            self.post_urls: List[str] = self.get_all_post_urls()

    def get_all_post_urls(self) -> List[str]:
        """
        Attempts to fetch URLs from sitemap.xml, falling back to feed.xml if necessary.
        """
        urls = self.fetch_urls_from_sitemap()
        if not urls:
            urls = self.fetch_urls_from_feed()
        return self.filter_urls(urls, self.keywords)

    def fetch_urls_from_sitemap(self) -> List[str]:
        """
        Fetches URLs from sitemap.xml.
        """
        sitemap_url = f"{self.base_substack_url}sitemap.xml"
        response = requests.get(sitemap_url)

        if not response.ok:
            print(f'Error fetching sitemap at {sitemap_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = [element.text for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        return urls

    def fetch_urls_from_feed(self) -> List[str]:
        """
        Fetches URLs from feed.xml.
        """
        print('Falling back to feed.xml. This will only contain up to the 22 most recent posts.')
        feed_url = f"{self.base_substack_url}feed.xml"
        response = requests.get(feed_url)

        if not response.ok:
            print(f'Error fetching feed at {feed_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = []
        for item in root.findall('.//item'):
            link = item.find('link')
            if link is not None and link.text:
                urls.append(link.text)

        return urls

    @staticmethod
    def filter_urls(urls: List[str], keywords: List[str]) -> List[str]:
        """
        This method filters out URLs that contain certain keywords
        """
        return [url for url in urls if all(keyword not in url for keyword in keywords)]

    @staticmethod
    def html_to_md(html_content: str) -> str:
        """
        This method converts HTML to Markdown
        """
        if not isinstance(html_content, str):
            raise ValueError("html_content must be a string")
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return h.handle(html_content)

    @staticmethod
    def save_to_file(filepath: str, content: str) -> None:
        """
        This method saves content to a file. Can be used to save HTML or Markdown
        """
        if not isinstance(filepath, str):
            raise ValueError("filepath must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        if os.path.exists(filepath):
            print(f"File already exists: {filepath}")
            return

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(content)

    @staticmethod
    def md_to_html(md_content: str) -> str:
        """
        This method converts Markdown to HTML
        """
        return markdown.markdown(md_content, extensions=['extra'])


    def save_to_html_file(self, filepath: str, content: str) -> None:
        """
        This method saves HTML content to a file with a link to an external CSS file.
        """
        if not isinstance(filepath, str):
            raise ValueError("filepath must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        # Calculate the relative path from the HTML file to the CSS file
        html_dir = os.path.dirname(filepath)
        css_path = os.path.relpath("./assets/css/essay-styles.css", html_dir)
        css_path = css_path.replace("\\", "/")  # Ensure forward slashes for web paths

        html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Markdown Content</title>
                <link rel="stylesheet" href="{css_path}">
            </head>
            <body>
                <main class="markdown-content">
                {content}
                </main>
            </body>
            </html>
        """

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(html_content)

    @staticmethod
    def get_filename_from_url(url: str, filetype: str = ".md") -> str:
        """
        Gets the filename from the URL (the ending)
        """
        if not isinstance(url, str):
            raise ValueError("url must be a string")

        if not isinstance(filetype, str):
            raise ValueError("filetype must be a string")

        if not filetype.startswith("."):
            filetype = f".{filetype}"

        return url.split("/")[-1] + filetype

    @staticmethod
    def combine_metadata_and_content(title: str, subtitle: str, date: str, like_count: str, content) -> str:
        """
        Combines the title, subtitle, and content into a single string with Markdown format
        """
        if not isinstance(title, str):
            raise ValueError("title must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        metadata = f"# {title}\n\n"
        if subtitle:
            metadata += f"## {subtitle}\n\n"
        metadata += f"**{date}**\n\n"
        metadata += f"**Likes:** {like_count}\n\n"

        return metadata + content

    def extract_post_data(self, soup: BeautifulSoup) -> Tuple[str, str, str, str, str]:
        """
        Converts substack post soup to markdown, returns metadata and content
        """
        title = soup.select_one("h1.post-title, h2").text.strip()  # When a video is present, the title is demoted to h2

        subtitle_element = soup.select_one("h3.subtitle")
        subtitle = subtitle_element.text.strip() if subtitle_element else ""

        date_element = soup.find(
            "div",
            class_="pencraft pc-reset _color-pub-secondary-text_3axfk_207 _line-height-20_3axfk_95 _font-meta_3axfk_131 _size-11_3axfk_35 _weight-medium_3axfk_162 _transform-uppercase_3axfk_242 _reset_3axfk_1 _meta_3axfk_442"
        )
        date = date_element.text.strip() if date_element else "Date not found"

        like_count_element = soup.select_one("a.post-ufi-button .label")
        like_count = (
            like_count_element.text.strip()
            if like_count_element and like_count_element.text.strip().isdigit()
            else "0"
        )

        content = str(soup.select_one("div.available-content"))
        md = self.html_to_md(content)
        md_content = self.combine_metadata_and_content(title, subtitle, date, like_count, md)
        return title, subtitle, like_count, date, md_content

    @abstractmethod
    def get_url_soup(self, url: str) -> str:
        raise NotImplementedError

    def save_essays_data_to_json(self, essays_data: list) -> None:
        """
        Saves essays data to a JSON file for a specific author.
        """
        data_dir = os.path.join(JSON_DATA_DIR)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        json_path = os.path.join(data_dir, f'{self.writer_name}.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as file:
                existing_data = json.load(file)
            essays_data = existing_data + [data for data in essays_data if data not in existing_data]
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(essays_data, f, ensure_ascii=False, indent=4)

    def scrape_posts(self, num_posts_to_scrape: int = 0) -> None:
        """
        Iterates over all posts and saves them as markdown and html files
        """
        essays_data = []
        count = 0
        total = num_posts_to_scrape if num_posts_to_scrape != 0 else len(self.post_urls)
        
        with tqdm(total=total, desc="Scraping posts") as pbar:
            for url in self.post_urls:
                try:
                    post_slug = url.split('/')[-1]
                    md_filename = self.get_filename_from_url(url, filetype=".md")
                    html_filename = self.get_filename_from_url(url, filetype=".html")
                    md_filepath = os.path.join(self.md_save_dir, md_filename)
                    html_filepath = os.path.join(self.html_save_dir, html_filename)

                    if not os.path.exists(md_filepath):
                        soup = self.get_url_soup(url)
                        if soup is None:
                            total += 1
                            continue
                            
                        title, subtitle, like_count, date, md = self.extract_post_data(soup)
                        
                        if self.download_images:
                            # Count images before downloading
                            total_images = count_images_in_markdown(md)
                            post_slug = url.split("/p/")[-1].split("/")[0]
                            
                            with tqdm(total=total_images, desc=f"Downloading images for {post_slug}", leave=False) as img_pbar:
                                md = process_markdown_images(md, self.writer_name, post_slug, img_pbar)
                                
                        self.save_to_file(md_filepath, md)
                        html_content = self.md_to_html(md)
                        self.save_to_html_file(html_filepath, html_content)

                        essays_data.append({
                            "title": title,
                            "subtitle": subtitle,
                            "like_count": like_count,
                            "date": date,
                            "file_link": md_filepath,
                            "html_link": html_filepath
                        })
                    else:
                        pbar.write(f"File already exists: {md_filepath}")
                        
                except Exception as e:
                    pbar.write(f"Error scraping post: {e}")
                    
                count += 1
                pbar.update(1)
                if num_posts_to_scrape != 0 and count == num_posts_to_scrape:
                    break
                    
        self.save_essays_data_to_json(essays_data=essays_data)
        generate_html_file(author_name=self.writer_name)


class SubstackScraper(BaseSubstackScraper):
    def __init__(self, base_substack_url: str, md_save_dir: str, html_save_dir: str, download_images: bool = False):
        super().__init__(base_substack_url, md_save_dir, html_save_dir, download_images)

    def get_url_soup(self, url: str) -> Optional[BeautifulSoup]:
        """
        Gets soup from URL using requests
        """
        try:
            page = requests.get(url, headers=None)
            soup = BeautifulSoup(page.content, "html.parser")
            if soup.find("h2", class_="paywall-title"):
                print(f"Skipping premium article: {url}")
                return None
            return soup
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


class PremiumSubstackScraper(BaseSubstackScraper):
    def __init__(
            self,
            base_substack_url: str,
            md_save_dir: str,
            html_save_dir: str,
            headless: bool = False,
            edge_path: str = '',
            edge_driver_path: str = '',
            user_agent: str = '',
            download_images: bool = False,
    ) -> None:
        super().__init__(base_substack_url, md_save_dir, html_save_dir, download_images)

        options = EdgeOptions()
        if headless:
            options.add_argument("--headless")
        if edge_path:
            options.binary_location = edge_path
        if user_agent:
            options.add_argument(f'user-agent={user_agent}')  # Pass this if running headless and blocked by captcha

        if edge_driver_path:
            service = Service(executable_path=edge_driver_path)
        else:
            service = Service(EdgeChromiumDriverManager().install())

        self.driver = webdriver.Edge(service=service, options=options)
        self.login()

    def login(self) -> None:
        """
        This method logs into Substack using Selenium
        """
        self.driver.get("https://substack.com/sign-in")
        sleep(3)

        signin_with_password = self.driver.find_element(
            By.XPATH, "//a[@class='login-option substack-login__login-option']"
        )
        signin_with_password.click()
        sleep(3)

        # Email and password
        email = self.driver.find_element(By.NAME, "email")
        password = self.driver.find_element(By.NAME, "password")
        email.send_keys(EMAIL)
        password.send_keys(PASSWORD)

        # Find the submit button and click it.
        submit = self.driver.find_element(By.XPATH, "//*[@id=\"substack-login\"]/div[2]/div[2]/form/button")
        submit.click()
        sleep(30)  # Wait for the page to load

        if self.is_login_failed():
            raise Exception(
                "Warning: Login unsuccessful. Please check your email and password, or your account status.\n"
                "Use the non-premium scraper for the non-paid posts. \n"
                "If running headless, run non-headlessly to see if blocked by Captcha."
            )

    def is_login_failed(self) -> bool:
        """
        Check for the presence of the 'error-container' to indicate a failed login attempt.
        """
        error_container = self.driver.find_elements(By.ID, 'error-container')
        return len(error_container) > 0 and error_container[0].is_displayed()

    def get_url_soup(self, url: str) -> BeautifulSoup:
        """
        Gets soup from URL using logged in selenium driver
        """
        try:
            self.driver.get(url)
            return BeautifulSoup(self.driver.page_source, "html.parser")
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape a Substack site or individual post.")
    parser.add_argument(
        "-u", "--url", type=str, help="The base URL of the Substack site to scrape."
    )
    parser.add_argument(
        "-d", "--directory", type=str, help="The directory to save scraped posts."
    )
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=0,
        help="The number of posts to scrape. If 0 or not provided, all posts will be scraped.",
    )
    parser.add_argument(
        "-p",
        "--premium",
        action="store_true",
        help="Include -p in command to use the Premium Substack Scraper with selenium.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Include -h in command to run browser in headless mode when using the Premium Substack "
        "Scraper.",
    )
    parser.add_argument(
        "--edge-path",
        type=str,
        default="",
        help='Optional: The path to the Edge browser executable (i.e. "path_to_msedge.exe").',
    )
    parser.add_argument(
        "--edge-driver-path",
        type=str,
        default="",
        help='Optional: The path to the Edge WebDriver executable (i.e. "path_to_msedgedriver.exe").',
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default="",
        help="Optional: Specify a custom user agent for selenium browser automation. Useful for "
        "passing captcha in headless mode",
    )
    parser.add_argument(
        "--html-directory",
        type=str,
        help="The directory to save scraped posts as HTML files.",
    )
    parser.add_argument(
        "--images",
        action="store_true",
        help="Download images and update markdown to use local paths"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    
    if args.directory is None:
        args.directory = BASE_MD_DIR
    
    if args.html_directory is None:
        args.html_directory = BASE_HTML_DIR

    if args.url:
        if args.premium:
            scraper = PremiumSubstackScraper(
                args.url,
                headless=args.headless,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                download_images=args.images
            )
        else:
            scraper = SubstackScraper(
                args.url,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                download_images=args.images
            )
        scraper.scrape_posts(args.number)

    else:  # Use the hardcoded values at the top of the file
        if USE_PREMIUM:
            scraper = PremiumSubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                edge_path=args.edge_path,
                edge_driver_path=args.edge_driver_path,
                download_images=args.images
            )
        else:
            scraper = SubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                download_images=args.images
            )
        scraper.scrape_posts(num_posts_to_scrape=NUM_POSTS_TO_SCRAPE)
    
    scraper.scrape_posts(args.number)

if __name__ == "__main__":
    main()
