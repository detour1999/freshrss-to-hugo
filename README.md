# RSS Feed to Hugo Blog Sync

This script synchronizes favorited articles from FreshRSS to a Hugo blog, generating AI summaries and managing git operations automatically.

## Environment Variables

The following environment variables are required to run the script. Create a `.env` file in the root directory using `.env.example` as a template.

### FreshRSS Configuration

- `FRESHRSS_URL`: Your FreshRSS instance URL
  - Example: `https://rss.yourdomain.com`
  - How to get: This is the base URL of your FreshRSS installation

- `FRESHRSS_USER`: Your FreshRSS username
  - How to get: This is your login username for FreshRSS

- `FRESHRSS_API_KEY`: Your FreshRSS API key
  - How to get: 
    1. Log into FreshRSS
    2. Go to User Settings
    3. Navigate to the "API Management" section
    4. Generate a new API key

### OpenAI Configuration

- `LLM_API_KEY`: Your OpenAI API key
  - How to get:
    1. Go to [OpenAI API Keys](https://platform.openai.com/api-keys)
    2. Create a new API key
    3. Copy the key (it will only be shown once)

### GitHub Configuration

- `GITHUB_TOKEN`: GitHub Personal Access Token
  - How to get:
    1. Go to [GitHub Settings > Developer Settings > Personal Access Tokens](https://github.com/settings/tokens)
    2. Click "Generate new token (classic)"
    3. Select scopes: `repo` (full control of private repositories)
    4. Generate and copy the token

## Setup

1. Clone this repository
2. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
3. Edit `.env` and fill in your configuration values
4. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Run the script:
```bash
python sync_favorites.py
```
