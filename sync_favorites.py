#!/usr/bin/env python3

import argparse
from datetime import datetime
from slugify import slugify
import yaml
import os
import openai
import requests
from dotenv import load_dotenv
from pathlib import Path
import glob
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import subprocess
from github import Github
from github.GithubException import GithubException
import time
import re

"""
Sync FreshRSS favorites to Hugo posts and update OPML file.
"""

def _get_freshrss_auth_token():
    """
    Get authentication token from FreshRSS using Google Reader API.

    Returns:
        tuple: (base_url, auth_token)
    """
    base_url = os.getenv("FRESHRSS_URL").rstrip('/')
    api_user = os.getenv("FRESHRSS_USER")
    api_key = os.getenv("FRESHRSS_API_KEY")

    auth_url = f"{base_url}/api/greader.php/accounts/ClientLogin"
    auth_data = {
        "Email": api_user,
        "Passwd": api_key
    }
    auth_response = requests.post(auth_url, data=auth_data)
    auth_response.raise_for_status()

    auth_token = None
    for line in auth_response.text.splitlines():
        if line.startswith('Auth='):
            auth_token = line[5:]
            break

    if not auth_token:
        raise ValueError("Failed to get authentication token")

    return base_url, auth_token

def fetch_new_favorites():
    """
    Retrieve new favorited articles from FreshRSS API using Google Reader API compatibility.

    Returns:
        list: List of article dictionaries containing metadata
    """
    base_url, auth_token = _get_freshrss_auth_token()

    headers = {
        "Authorization": f"GoogleLogin auth={auth_token}"
    }

    # Construct URL for starred items
    starred_url = f"{base_url}/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/starred"
    params = {
        "n": "50",  # Number of items to fetch as string
        "output": "json",
    }

    response = requests.get(starred_url, headers=headers, params=params)
    response.raise_for_status()
    data = response.json()

    articles = []
    for item in data.get('items', []):
        # Extract author from different possible locations
        author = None
        if 'author' in item:
            author = item['author']
        elif 'origin' in item and 'title' in item['origin']:
            author = item['origin']['title']

        # Parse the published date
        published_date = datetime.fromtimestamp(item['published'])

        # Extract content from different possible locations
        content = item.get('content', {}).get('content', '')
        if not content and 'summary' in item:
            content = item['summary'].get('content', '')

        article = {
            "title": item.get('title', 'Untitled'),
            "author": author or 'Unknown',
            "content": content,
            "link": item.get('alternate', [{'href': None}])[0]['href'],
            "feed_name": item.get('origin', {}).get('title', 'Unknown Feed'),
            "published_date": published_date
        }
        articles.append(article)

    return articles

def call_llm_for_summary(article_content):
    """
    Generate article summary and metadata using OpenAI API.

    Args:
        article_content (str): The full article content

    Returns:
        dict: Contains summary, tags, and categories
    """
    # Initialize OpenAI client
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY environment variable not set")

    client = openai.OpenAI(api_key=api_key)

    # Prepare the prompt
    prompt = f"""Analyze this article and provide:
1. A concise summary (2-3 sentences)
2. 3-5 relevant tags
3. 1-2 broad categories

Article content:
{article_content}

Respond in this exact JSON format:
{{
    "summary": "your summary here",
    "tags": ["tag1", "tag2", "tag3"],
    "categories": ["category1", "category2"]
}}"""

    # Call the API
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that analyzes articles and provides structured summaries."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        response_format={ "type": "json_object" }
    )

    # Parse and return the response
    return response.choices[0].message.content

def generate_markdown(article, llm_summary):
    """
    Generate Hugo-compatible Markdown file for an article.

    Args:
        article (dict): Article metadata from FreshRSS
        llm_summary (dict): AI-generated summary and metadata

    Returns:
        tuple: (markdown_content, filename)
    """
    # Prepare front matter
    front_matter = {
        "title": article["title"],
        "author": article["author"],
        "link": article["link"],
        "source": article["feed_name"],
        "summary": llm_summary["summary"],
        "tags": llm_summary.get("tags", ["uncategorized"]),
        "categories": llm_summary.get("categories", ["general"]),
        "date": article["published_date"].strftime("%Y-%m-%d")
    }

    # Generate filename
    date_prefix = article["published_date"].strftime("%Y-%m-%d")
    slug = slugify(article["title"])
    filename = f"{date_prefix}-{slug}.md"

    # Create markdown content
    markdown = "---\n"
    markdown += yaml.dump(front_matter, allow_unicode=True, sort_keys=False)
    markdown += "---\n\n"
    markdown += article["content"]

    return markdown, filename

def _check_duplicate_link(link, content_dir):
    """
    Check if an article with the given link already exists.

    Args:
        link (str): The article's URL
        content_dir (Path): Path to content directory

    Returns:
        bool: True if duplicate exists, False otherwise
    """
    for md_file in content_dir.glob("*.md"):
        with md_file.open() as f:
            content = f.read()
            # Find the YAML front matter between --- markers
            if content.startswith("---"):
                front_matter_end = content.find("---", 3)
                if front_matter_end != -1:
                    front_matter = yaml.safe_load(content[3:front_matter_end])
                    if front_matter.get("link") == link:
                        return True
    return False

def write_markdown_to_repo(filename, markdown_content, repo_path: Path):
    """
    Write markdown content to the repository.

    Args:
        filename (str): Name of the markdown file
        markdown_content (str): The formatted markdown content
        repo_path (Path): Path to the repository root

    Returns:
        bool: True if file was written, False if skipped due to duplicate
    """
    content_dir = repo_path / "content" / "reading"

    # Ensure directory exists
    content_dir.mkdir(parents=True, exist_ok=True)

    # Extract link from markdown content for duplicate checking
    front_matter_end = markdown_content.find("---", 3)
    front_matter = yaml.safe_load(markdown_content[3:front_matter_end])
    link = front_matter.get("link")

    # Check for duplicates
    if _check_duplicate_link(link, content_dir):
        print(f"Skipping {filename} - article already exists")
        return False

    # Write the file
    output_path = content_dir / filename
    output_path.write_text(markdown_content)
    print(f"Written {filename}")
    return True

def update_opml_file(repo_path):
    """
    Fetch OPML file from FreshRSS and save it to Hugo static directory.

    Args:
        repo_path (Path): Path to the repository root

    Returns:
        bool: True if successful, False otherwise
    """
    base_url, auth_token = _get_freshrss_auth_token()

    headers = {
        "Authorization": f"GoogleLogin auth={auth_token}"
    }

    opml_url = f"{base_url}/api/greader.php/subscriptions/export"
    response = requests.get(opml_url, headers=headers)
    response.raise_for_status()

    # Ensure static directory exists
    static_dir = Path(repo_path) / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    opml_path = static_dir / "myfeeds.opml"

    # Check if content is different before writing
    if opml_path.exists():
        with opml_path.open('rb') as f:
            current_content = f.read()
            if current_content == response.content:
                print("OPML file is already up to date")
                return True

    # Write new OPML file
    with opml_path.open('wb') as f:
        f.write(response.content)

    print(f"Updated {opml_path}")
    return True

def auto_merge_pr_if_checks_pass(pr_url):
    """
    Monitor PR status and auto-merge if all checks pass.

    Args:
        pr_url (str): URL of the pull request to monitor

    Returns:
        bool: True if merged successfully, False otherwise
    """
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable not set")

    try:
        # Extract repo and PR number from URL
        match = re.match(r"https://github.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
        if not match:
            raise ValueError(f"Invalid PR URL format: {pr_url}")

        repo_path, pr_number = match.groups()
        pr_number = int(pr_number)

        # Initialize GitHub client
        g = Github(github_token)
        repo = g.get_repo(repo_path)
        pr = repo.get_pull(pr_number)

        # Poll for status checks (max 10 minutes)
        for _ in range(60):
            combined_status = repo.get_combined_status(pr.head.sha)
            checks = list(pr.get_checks())

            # Check if Netlify deploy is successful
            netlify_check = next((check for check in checks if 'netlify' in check.name.lower()), None)

            if combined_status.state == 'success' and \
               (not netlify_check or netlify_check.conclusion == 'success'):
                # All checks passed, merge the PR
                pr.merge(
                    merge_method='merge',
                    commit_message="Merging auto-generated reading articles."
                )
                print(f"Successfully merged PR {pr_url}")
                return True

            if combined_status.state == 'failure' or \
               (netlify_check and netlify_check.conclusion == 'failure'):
                print(f"Checks failed for PR {pr_url}, manual review required")
                return False

            # Wait 10 seconds before next check
            time.sleep(10)

        print(f"Timeout waiting for checks on PR {pr_url}")
        return False

    except GithubException as e:
        print(f"Failed to auto-merge PR: {e}")
        return False

def create_pull_request(repo_url, branch_name, base_branch="main"):
    """
    Create a GitHub pull request for the new articles.

    Args:
        repo_url (str): GitHub repository URL (e.g., "owner/repo")
        branch_name (str): Name of the branch to create PR from
        base_branch (str): Target branch for the PR

    Returns:
        str: URL of the created pull request, or None if failed
    """
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GITHUB_TOKEN environment variable not set")

    try:
        # Initialize GitHub client
        g = Github(github_token)
        repo = g.get_repo(repo_url)

        # Check if PR already exists
        existing_prs = repo.get_pulls(state='open',
                                    head=f"{repo.owner.login}:{branch_name}",
                                    base=base_branch)
        if existing_prs.totalCount > 0:
            print(f"PR already exists for branch {branch_name}")
            return existing_prs[0].html_url

        # Create new PR
        pr_title = f"New Reading Articles - {datetime.now().strftime('%Y-%m-%d')}"
        pr_body = "Automatically generated reading articles."
        pr = repo.create_pull(title=pr_title,
                            body=pr_body,
                            head=branch_name,
                            base=base_branch)

        print(f"Created PR: {pr.html_url}")
        return pr.html_url

    except GithubException as e:
        print(f"Failed to create PR: {e}")
        return None

def ensure_hugo_repo():
    """
    Ensure Hugo repository is checked out and up to date.

    Returns:
        Path: Path to the Hugo repository
    """
    repo_name = os.getenv("REPO_NAME")
    if not repo_name:
        raise ValueError("REPO_NAME environment variable not set")

    repo_path = Path(".hugo_repo")

    try:
        if not repo_path.exists():
            # Clone the repository
            subprocess.run(
                ["git", "clone", f"https://github.com/{repo_name}.git", str(repo_path)],
                check=True
            )
        else:
            # Update existing repository
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_path,
                check=True
            )
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_path,
                check=True
            )
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=repo_path,
                check=True
            )

        return repo_path

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to setup Hugo repository: {e}")

def create_git_branch_and_commit(repo_path, branch_name=None):
    """
    Create a new git branch, commit changes, and push to remote.

    Args:
        repo_path (str): Path to the repository root
        branch_name (str, optional): Name for the new branch

    Returns:
        bool: True if successful, False otherwise
    """
    repo_path = Path(repo_path)

    # Check if there are any changes to commit
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True
    )

    if not result.stdout.strip():
        print("No changes to commit")
        return False

    # Generate branch name if not provided
    if branch_name is None:
        branch_name = f"reading-update-{datetime.now().strftime('%Y%m%d')}"

    try:
        # Create and checkout new branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_path,
            check=True
        )

        # Stage changes
        subprocess.run(
            ["git", "add", "content/reading/*", "myfeeds.opml"],
            cwd=repo_path,
            check=True
        )

        # Commit changes
        subprocess.run(
            ["git", "commit", "-m", "Add new reading articles"],
            cwd=repo_path,
            check=True
        )

        # Push to remote
        subprocess.run(
            ["git", "push", "origin", branch_name],
            cwd=repo_path,
            check=True
        )

        print(f"Changes committed and pushed to branch: {branch_name}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Git operation failed: {e}")
        return False

def verify_env_vars(required_vars):
    """Verify all required environment variables are set."""
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

def show_favorites(count=5):
    """Display recent favorites without processing them."""
    articles = fetch_new_favorites()
    if not articles:
        print("No articles found.")
        return

    print(f"\nShowing {min(count, len(articles))} recent favorites:\n")
    for i, article in enumerate(articles[:count], 1):
        print(f"{i}. {article['title']}")
        print(f"   Author: {article['author']}")
        print(f"   Feed: {article['feed_name']}")
        print(f"   Published: {article['published_date']}")
        print(f"   Link: {article['link']}\n")

def test_llm():
    """Test LLM summary generation with a sample article."""
    sample_content = """
    This is a test article about Python programming.
    Python is a versatile programming language used in web development,
    data science, and automation. Its clean syntax and extensive library
    ecosystem make it popular among developers.
    """
    try:
        summary = call_llm_for_summary(sample_content)
        print("\nLLM Test Results:")
        print(yaml.dump(summary, allow_unicode=True))
    except Exception as e:
        print(f"LLM test failed: {e}")

def main():
    """Main sync process."""
    parser = argparse.ArgumentParser(
        description="Sync FreshRSS favorites to Hugo",
        epilog="Example: uv run sync_favorites.py --show-favorites 5"
    )
    parser.add_argument('--show-favorites', type=int, nargs='?', const=5, metavar='N',
                      help='Show N recent favorites (default: 5)')
    parser.add_argument('--test-llm', action='store_true',
                      help='Test LLM summary generation')
    parser.add_argument('--sync', action='store_true',
                      help='Run full sync process')
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    try:
        if args.show_favorites:
            # Only need FreshRSS vars
            verify_env_vars(["FRESHRSS_URL", "FRESHRSS_USER", "FRESHRSS_API_KEY"])
            show_favorites(args.show_favorites)
            return

        if args.test_llm:
            # Only need LLM vars
            verify_env_vars(["LLM_API_KEY"])
            test_llm()
            return

        if args.sync:
            # Need all vars for full sync
            verify_env_vars([
                "FRESHRSS_URL",
                "FRESHRSS_USER",
                "FRESHRSS_API_KEY",
                "LLM_API_KEY",
                "GITHUB_TOKEN",
                "REPO_NAME"
            ])

            print("Sync process started.")

            # Ensure Hugo repository is ready
            repo_path = ensure_hugo_repo()

            # Fetch new articles
            articles = fetch_new_favorites()
            if not articles:
                print("No new articles found.")
                return

            new_articles_added = False

            # Process each article
            for article in articles:
                try:
                    # Generate summary using LLM
                    llm_result = call_llm_for_summary(article["content"])

                    # Generate markdown
                    markdown_content, filename = generate_markdown(article, llm_result)

                    # Write to repo
                    if write_markdown_to_repo(filename, markdown_content, repo_path):
                        new_articles_added = True

                except Exception as e:
                    print(f"Error processing article '{article.get('title', 'Unknown')}': {e}")
                    continue

            # Update OPML file
            update_opml_file(repo_path)

            # Create PR if new articles were added
            if new_articles_added:
                branch_name = f"reading-update-{datetime.now().strftime('%Y%m%d')}"
                if create_git_branch_and_commit(repo_path, branch_name):
                    pr_url = create_pull_request(os.getenv("REPO_NAME"), branch_name)
                    if pr_url:
                        auto_merge_pr_if_checks_pass(pr_url)
                        print("Successfully processed new articles and created PR")

            print("Sync process completed successfully")
            return

        # If no arguments provided, show help
        parser.print_help()

    except Exception as e:
        print(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
