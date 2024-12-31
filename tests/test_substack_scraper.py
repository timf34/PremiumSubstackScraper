import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from substack_scraper import (
    BASE_IMAGE_DIR,
    SubstackScraper,
    clean_linked_images,
    count_images_in_markdown,
    sanitize_filename,
    process_markdown_images,
)

@pytest.fixture
def mock_html_content():
    return """
    <html>
        <body>
            <h1 class="post-title">Test Post</h1>
            <h3 class="subtitle">Test Subtitle</h3>
            <div class="available-content">
                <p>Test content with image:</p>
                <img src="https://substackcdn.com/image/fetch/w_720,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2Ftest1.jpg" />
                <img src="https://substackcdn.com/image/fetch/w_720,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2Ftest2.jpg" />
            </div>
        </body>
    </html>
    """

@pytest.fixture
def mock_image_response():
    return b"fake-image-data"

@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory structure for tests"""
    md_dir = tmp_path / "substack_md_files"
    html_dir = tmp_path / "substack_html_pages"
    img_dir = tmp_path / "substack_images"
    
    md_dir.mkdir()
    html_dir.mkdir()
    img_dir.mkdir()
    
    return tmp_path

def test_count_images_in_markdown():
    markdown_content = """
    Here's an image:
    ![Test](https://substackcdn.com/image/fetch/test1.jpg)
    And another:
    ![Test2](https://substackcdn.com/image/fetch/test2.jpg)
    And some text.
    """
    assert count_images_in_markdown(markdown_content) == 2

def test_sanitize_filename():
    url = "https://substackcdn.com/image/fetch/w_720/test%2Fimage.jpg"
    filename = sanitize_filename(url)
    assert isinstance(filename, str)
    assert filename.endswith(".jpg")
    assert "/" not in filename
    assert "\\" not in filename

def test_process_markdown_images(temp_dir, monkeypatch):
    markdown_content = """
    ![Test](https://substackcdn.com/image/fetch/test1.jpg)
    ![Test2](https://substackcdn.com/image/fetch/test2.jpg)
    """
    
    # Delete testauthor folder if exists
    test_author_dir = Path(BASE_IMAGE_DIR) / "testauthor"
    if test_author_dir.exists():
        shutil.rmtree(test_author_dir)
    
    # Mock requests.get
    mock_get = Mock()
    mock_get.return_value.iter_content = lambda chunk_size: []
    mock_get.return_value.status_code = 200
    monkeypatch.setattr("requests.get", mock_get)
    
    # Mock tqdm
    mock_tqdm = Mock()
    mock_tqdm.update = Mock()
    
    processed_md = process_markdown_images(
        markdown_content,
        "testauthor",
        "testpost",
        mock_tqdm
    )
    
    assert "../substack_images/" in processed_md
    assert mock_get.called
    assert mock_tqdm.update.called

def test_scraper_initialization(temp_dir):
    scraper = SubstackScraper(
        "https://test.substack.com",
        str(temp_dir / "substack_md_files"),
        str(temp_dir / "substack_html_pages")
    )
    assert scraper.writer_name == "test"
    assert os.path.exists(scraper.md_save_dir)
    assert os.path.exists(scraper.html_save_dir)

@patch("requests.get")
def test_scraper_single_post(mock_get, temp_dir, mock_html_content):
    mock_get.return_value.ok = True
    mock_get.return_value.content = mock_html_content.encode()
    
    scraper = SubstackScraper(
        "https://test.substack.com",
        str(temp_dir / "substack_md_files"),
        str(temp_dir / "substack_html_pages")
    )
    
    url = "https://test.substack.com/p/test-post"
    soup = scraper.get_url_soup(url)
    title, subtitle, like_count, date, md = scraper.extract_post_data(soup)
    
    assert title == "Test Post"
    assert subtitle == "Test Subtitle"
    assert isinstance(md, str)

def test_premium_content_handling(temp_dir, monkeypatch):
    html_with_paywall = """
    <html>
        <body>
            <h2 class="paywall-title">Premium Content</h2>
        </body>
    </html>
    """
    
    # Mock requests.get
    mock_get = Mock()
    mock_get.return_value.content = html_with_paywall.encode()
    monkeypatch.setattr("requests.get", mock_get)
    
    scraper = SubstackScraper(
        "https://test.substack.com",
        str(temp_dir / "substack_md_files"),
        str(temp_dir / "substack_html_pages")
    )
    
    result = scraper.get_url_soup("https://test.substack.com/p/premium-post")
    assert result is None

def test_image_download_error_handling(temp_dir, monkeypatch):
    # Mock requests.get to simulate network error
    def mock_get(*args, **kwargs):
        raise Exception("Network error")
    
    monkeypatch.setattr("requests.get", mock_get)
    
    markdown_content = "![Test](https://substackcdn.com/image/fetch/test.jpg)"
    mock_tqdm = Mock()
    
    # Should not raise exception but log error
    processed_md = process_markdown_images(
        markdown_content,
        "testauthor",
        "testpost",
        mock_tqdm
    )
    
def test_directory_structure(temp_dir):
    scraper = SubstackScraper(
        "https://test.substack.com",
        str(temp_dir / "substack_md_files"),
        str(temp_dir / "substack_html_pages")
    )
    
    assert Path(scraper.md_save_dir).exists()
    assert Path(scraper.html_save_dir).exists()
    assert "test" in str(scraper.md_save_dir)
    assert "test" in str(scraper.html_save_dir)

@pytest.mark.parametrize("test_case", [
    {
        "name": "basic_cleaning",
        "input": """
        Some text here
        [![Image 1](/img/test/image1.png)](/img/test/image1.png)
        More text
        [![](/img/test/image2.jpg)](/img/test/image2.jpg)
        Final text
        """,
        "expected": """
        Some text here
        ![Image 1](/img/test/image1.png)
        More text
        ![](/img/test/image2.jpg)
        Final text
        """
    },
    {
        "name": "mixed_content",
        "input": """
        Regular link: [Link text](https://example.com)
        Regular image: ![Alt text](/img/regular.jpg)
        Linked image: [![Image](/img/linked/test.png)](/img/linked/test.png)
        """,
        "expected": """
        Regular link: [Link text](https://example.com)
        Regular image: ![Alt text](/img/regular.jpg)
        Linked image: ![Image](/img/linked/test.png)
        """
    },
    {
        "name": "substack_cdn",
        "input": """
        [![](/img/test/image1.jpg)](https://substackcdn.com/image/fetch/test1.jpg)
        [![Alt text](https://substackcdn.com/image/fetch/test2.jpg)](https://substackcdn.com/image/fetch/test2.jpg)
        """,
        "expected": """
        ![](/img/test/image1.jpg)
        ![Alt text](https://substackcdn.com/image/fetch/test2.jpg)
        """
    },
    {
        "name": "no_changes_needed",
        "input": """
        # Header
        Regular text
        ![Image](/img/test.jpg)
        [Link](https://example.com)
        """,
        "expected": """
        # Header
        Regular text
        ![Image](/img/test.jpg)
        [Link](https://example.com)
        """
    },
    {
        "name": "empty_content",
        "input": "",
        "expected": ""
    },
    {
        "name": "preserve_newlines",
        "input": """
        Line 1

        [![Image](/test.jpg)](/test.jpg)

        Line 2
        """,
        "expected": """
        Line 1

        ![Image](/test.jpg)

        Line 2
        """
    },
    {
        "name": "special_characters",
        "input": """
        [![Test & Demo](/img/test&demo.jpg)](/img/test&demo.jpg)
        [![Spaces Test](/img/spaces%20test.jpg)](/img/spaces%20test.jpg)
        """,
        "expected": """
        ![Test & Demo](/img/test&demo.jpg)
        ![Spaces Test](/img/spaces%20test.jpg)
        """
    }
])
def test_clean_linked_images(test_case):
    """
    Parametrized test for cleaning linked images in markdown content.
    Tests various scenarios including basic cleaning, mixed content,
    CDN URLs, empty content, and special characters.
    """
    result = clean_linked_images(test_case["input"])
    assert result.strip() == test_case["expected"].strip()

def test_clean_linked_images_integration(temp_dir, monkeypatch):
    """Test integration with markdown processing pipeline."""
    # Initialize scraper with images=False
    scraper = SubstackScraper(
        base_substack_url="https://on.substack.com",
        md_save_dir=str(temp_dir / "substack_md_files"),
        html_save_dir=str(temp_dir / "substack_html_pages"),
        download_images=True
    )
    # Run scraper
    scraper.scrape_posts(num_posts_to_scrape=1)
    
    # # Check that markdown files were created
    md_files = list(Path(temp_dir / "substack_md_files" / "on").glob("*.md"))
    assert len(md_files) > 0
    
    # Verify markdown content still contains original image URLs
    with open(md_files[0], 'r') as f:
        content = f.read()
        assert "[![" not in content
        assert "](" in content
        assert "![" in content
        
def test_scraper_without_images_integration(temp_dir):
    """Test that images are not downloaded when --images flag is not set"""
    
    # Initialize scraper with images=False
    scraper = SubstackScraper(
        base_substack_url="https://on.substack.com",
        md_save_dir=str(temp_dir / "substack_md_files"),
        html_save_dir=str(temp_dir / "substack_html_pages"),
        download_images=False
    )
    
    # Run scraper
    scraper.scrape_posts(num_posts_to_scrape=1)
    
    # # Check that markdown files were created
    md_files = list(Path(temp_dir / "substack_md_files" / "on").glob("*.md"))
    assert len(md_files) > 0
    
    # Check that no image directory was created
    img_dir = temp_dir / "substack_images" / "on"
    assert not img_dir.exists()
    
    # Verify markdown content still contains original image URLs
    with open(md_files[0], 'r') as f:
        content = f.read()
        assert "https://substackcdn.com/image/fetch" in content

if __name__ == "__main__":
    pytest.main(["-v"])
