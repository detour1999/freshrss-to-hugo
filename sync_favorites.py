#!/usr/bin/env python3

from datetime import datetime
from slugify import slugify
import yaml
import os
import openai
from pathlib import Path
import glob
import opml
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

def fetch_new_favorites():
    """
    Retrieve new favorited articles from FreshRSS API.
    
    Returns:
        list: List of article dictionaries containing metadata
    """
    # Mock data for development
    return [
        {
            "title": "Understanding Python's Asyncio",
            "author": "Real Python",
            "content": "Asyncio is a library to write concurrent code using the async/await syntax...",
            "link": "https://realpython.com/async-io-python/",
            "feed_name": "Real Python",
            "published_date": datetime(2024, 1, 10, 9, 30)
        },
        {
            "title": "The Future of Web Development",
            "author": "Sarah Smith",
            "content": "Web development is rapidly evolving with new frameworks and tools...",
            "link": "https://techblog.com/future-web-dev",
            "feed_name": "Tech Blog",
            "published_date": datetime(2024, 1, 12, 15, 45)
        }
    ]

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

def write_markdown_to_repo(filename, markdown_content, repo_path):
    """
    Write markdown content to the repository.
    
    Args:
        filename (str): Name of the markdown file
        markdown_content (str): The formatted markdown content
        repo_path (str): Path to the repository root
        
    Returns:
        bool: True if file was written, False if skipped due to duplicate
    """
    content_dir = Path(repo_path) / "content" / "reading"
    
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
    Update or create OPML file with latest feed data.
    
    Args:
        repo_path (str): Path to the repository root
        
    Returns:
        bool: True if successful, False otherwise
    """
    opml_path = Path(repo_path) / "myfeeds.opml"
    
    # Create basic OPML structure if file doesn't exist
    if not opml_path.exists():
        root = ET.Element("opml", version="2.0")
        head = ET.SubElement(root, "head")
        ET.SubElement(head, "title").text = "My RSS Feeds"
        ET.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        ET.SubElement(root, "body")
        tree = ET.ElementTree(root)
        tree.write(opml_path, encoding='utf-8', xml_declaration=True)
    
    # Update timestamp in existing file
    tree = ET.parse(opml_path)
    root = tree.getroot()
    head = root.find("head")
    date_modified = head.find("dateModified")
    if date_modified is None:
        date_modified = ET.SubElement(head, "dateModified")
    date_modified.text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    
    # Write updated file
    tree.write(opml_path, encoding='utf-8', xml_declaration=True)
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

def main():
    """Main sync process."""
    print("Sync process started.")
    
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
            if write_markdown_to_repo(filename, markdown_content, "."):
                new_articles_added = True
                
        except Exception as e:
            print(f"Error processing article '{article.get('title', 'Unknown')}': {e}")
            continue
    
    # Update OPML file
    update_opml_file(".")
    
    # Create PR if new articles were added
    if new_articles_added:
        branch_name = f"reading-update-{datetime.now().strftime('%Y%m%d')}"
        if create_git_branch_and_commit(".", branch_name):
            pr_url = create_pull_request("owner/repo", branch_name)
            if pr_url:
                auto_merge_pr_if_checks_pass(pr_url)
                print("Successfully processed new articles and created PR")
    
    print("Sync process completed successfully")

if __name__ == "__main__":
    main()
