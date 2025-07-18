# 📹 Webex Recordings Downloader

Simple application to collect all Webex recordingIds and associated hostEmails and then download all recordings locally. This is meant to demonstrate the code logic for bulk downloading Webex Meeting recordings using the REST APIs.

It is a two step process which requires the app to be ran twice.

On first run choose option 1 and provide your Webex site URL, for example _sitename.webex.com_, and enter the number of weeks you want to pull recordings for.

- This collects all recordingIds and hostEmails and stores them in the recordings.csv file.
- The app will terminate itself after completion.

Run the app again choose option 2.

- This will download all recordings that were retrived from step 1 and save them to the "Downloaded-Recordings" folder.

## ✨ Features

- **📊 Bulk Recording Retrieval** - Collect all recording metadata from specified time periods
- **💾 CSV Data Export** - Store recording IDs and host emails for batch processing
- **⬇️ Automated Downloads** - Download all recordings to local directory structure
- **🔄 Token Management** - Automatic token refresh with OAuth integration support
- **⏱️ Rate Limit Handling** - Built-in retry logic for API rate limiting
- **📅 Date Range Support** - Flexible time period selection (weeks-based)
- **🏢 Multi-Site Support** - Works with any Webex site URL
- **🔐 Dual Authentication** - Personal access tokens or OAuth integrations

## 🚀 Install and Run

### Prerequisites

- Python 3.7 or later
- Valid Webex access token or OAuth integration
- Admin permissions for recording access

### Installation

Clone project:

```bash
git clone https://github.com/WebexSamples/WebexRecordingsDownloader.git
cd WebexRecordingsDownloader
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run app:

```bash
python recordings.py
```

## ⚙️ Setup

### Option 1: Personal Access Token (Quick Start)

- Login to the developer portal and copy your personal access token from https://developer.webex.com/docs/getting-started.
- When you first run the app it will ask you to provide the token you copied from the above page.
- This will allow the app to run for 12 hours as that is how long the personal access token is only valid for. You would need to login to the developer portal again to get a new personal access token if the current one has expired.

### Option 2: OAuth Integration (Recommended for Production)

This will allow the app to handle token refreshes automatically:

- Create an [Integration](https://developer.webex.com/docs/integrations) or [Service App](https://developer.webex.com/docs/service-apps) with the admin and compliance related recording scopes.
- Generate your access and refresh tokens
- Rename the [.env.local](.env.local) file to .env. And add .env to the .gitignore file
- Add your Client ID, Client Secret and Refresh Token to the .env file.
- You can also add your Access Token to the [token.json](token.json) file but the app will also ask you to enter one at first run if you haven't added it to the token.json file.

## 📖 Usage Guide

### Step 1: Collect Recording Metadata

1. **Run the application:**
   ```bash
   python recordings.py
   ```

2. **Select Option 1:**
   - Choose "1 - List all recordings and save to .csv file"
   - Enter your Webex site URL (e.g., `sitename.webex.com`)
   - Specify the number of weeks to retrieve recordings for

3. **Wait for completion:**
   - The app will collect all recording IDs and host emails
   - Data is stored in `recordings.csv`
   - Application will terminate automatically

### Step 2: Download Recordings

1. **Run the application again:**
   ```bash
   python recordings.py
   ```

2. **Select Option 2:**
   - Choose "2 - Download recordings"
   - The app will process all entries from `recordings.csv`
   - Downloads are saved to `Downloaded-Recordings/` folder

### Example Workflow

```bash
# First run - collect metadata
python recordings.py
> 1
> sitename.webex.com
> 4
# App collects 4 weeks of recordings and exits

# Second run - download files
python recordings.py
> 2
# App downloads all recordings from CSV
```

## 🏗️ Project Structure

```
WebexRecordingsDownloader/
├── recordings.py              # Main application entry point
├── list_recordings.py         # Recording metadata collection logic
├── download_recordings.py     # Recording download logic
├── requirements.txt           # Python dependencies
├── .env.local                 # Environment variables template
├── token.json                 # Access token storage
├── recordings.csv             # Generated recording metadata
├── Downloaded-Recordings/     # Downloaded files directory
├── package.json               # Development tooling configuration
├── LICENSE                    # Cisco Sample Code License
└── README.md                  # This documentation
```

### Core Components

| Component | Description | File Location |
|-----------|-------------|---------------|
| **Main Controller** | User interface and workflow coordination | [`recordings.py`](recordings.py) |
| **Metadata Collector** | Recording discovery and CSV generation | [`list_recordings.py`](list_recordings.py) |
| **Download Engine** | File download and storage management | [`download_recordings.py`](download_recordings.py) |
| **Token Manager** | OAuth token refresh and authentication | [`list_recordings.py`](list_recordings.py) lines 8-23 |

## 🔧 Code Implementation

### Main Application Flow

```python
# recordings.py - Main application logic
print("Select an option:")
print("1 - List all recordings and save to .csv file.")
print("2 - Download recordings.\n")

if choice == "1":
    site_url = input("Enter the Webex site URL...")
    weeks = input("Enter the number of weeks...")
    result = list_recordings.list(headers, site_url, weeks)
elif choice == "2":
    result = download_recordings.getDownloadLinks(headers)
```

### Recording Metadata Collection

```python
# list_recordings.py - Collect recording data
def list(headers, site_url, weeks):
    to_time = datetime.datetime.now().replace(microsecond=0)
    from_time = to_time - datetime.timedelta(days=30)
    end_time = from_time - datetime.timedelta(weeks=int(weeks))
    
    url = "https://webexapis.com/v1/admin/recordings?siteUrl={0}&max=100&from={1}&to={2}".format(
        site_url, from_time, to_time)
    
    response = requests.get(url, headers=headers)
    # Process pagination and store results
```

### File Download Process

```python
# download_recordings.py - Download recordings
def getDownloadLinks(headers):
    with open('recordings.csv', 'r') as csvfile:
        recs = csv.reader(csvfile)
        for row in recs:
            id = row[0]
            hostEmail = row[1].replace('@','%40').replace("+", "%2B")
            
            # Get temporary download link
            url = f'https://webexapis.com/v1/recordings/{id}?hostEmail={hostEmail}'
            result = requests.get(url, headers=headers)
            
            # Download and save file
            recording = requests.get(recordingDownloadLink)
            fileName = recording.headers.get('Content-Disposition').split("''")[1]
            with open(f"Downloaded-Recordings/{fileName}", 'wb') as file:
                file.write(recording.content)
```

### Token Refresh Implementation

```python
# list_recordings.py - Automatic token refresh
def token_refresh():
    url = "https://webexapis.com/v1/access_token"
    data = {
        "grant_type": "refresh_token",
        "client_id": os.getenv('client_id'),
        "client_secret": os.getenv('client_secret'),
        "refresh_token": os.getenv('refresh_token')
    }
    newToken = requests.post(url, json=data, headers=refresh_headers)
    return newToken
```

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# OAuth Integration Settings
client_id=your_client_id_here
client_secret=your_client_secret_here  
refresh_token=your_refresh_token_here
```

### Token Storage (token.json)

```json
{
  "token": "your_access_token_here"
}
```

### Dependencies (requirements.txt)

```txt
python-dotenv==1.0.1
Requests==2.32.3
```

### Required OAuth Scopes

For OAuth integrations, ensure your app has these scopes:
- `spark:admin_recordings_read` - Read recording metadata
- `spark:admin_recordings_write` - Access recording download links
- `compliance:recordings_read` - Compliance access to recordings

## 🔐 Authentication Options

### Personal Access Token

- **Pros**: Quick setup, immediate use
- **Cons**: 12-hour expiration, manual renewal required
- **Use Case**: Testing, one-time bulk downloads

```python
# Manual token entry when prompted
token = input("> ")
bearer = {"token": token}
```

### OAuth Integration

- **Pros**: Automatic token refresh, long-term use
- **Cons**: More complex setup, requires app registration
- **Use Case**: Production environments, scheduled downloads

```python
# Automatic refresh when token expires
if response.status_code == 401:
    newTokenCreate = token_refresh()
    newToken = res['access_token']
```

## 📊 Output and Results

### Generated Files

| File | Description | Content |
|------|-------------|---------|
| **recordings.csv** | Recording metadata | `recordingId,hostEmail` pairs |
| **Downloaded-Recordings/** | Downloaded files | `.mp4`, `.m4a`, or other recording formats |
| **token.json** | Current access token | JSON with current bearer token |

### CSV Format Example

```csv
Y2lzY29zcGFyazovL3VzL1JFQ09SRElORy8xMjM0NTY3ODkw,user1@company.com
Y2lzY29zcGFyazovL3VzL1JFQ09SRElORy85ODc2NTQzMjEw,user2@company.com
```

### Downloaded File Naming

Files are saved with their original names as provided by the Webex API:
- `Meeting_Recording_2024-01-15_14-30-00.mp4`
- `Audio_Only_2024-01-16_09-15-30.m4a`

## 🚨 Error Handling

### Rate Limiting

The application handles Webex API rate limits automatically:

```python
if response.status_code == 429:
    retry_after = response.headers.get("retry-after")
    print(f"Rate limited. Waiting {retry_after} seconds.")
    time.sleep(int(retry_after))
```

### Token Expiration

```python
if response.status_code == 401:
    if os.getenv('client_id'):
        # Automatic refresh
        newTokenCreate = token_refresh()
    else:
        # Manual intervention required
        print("Add integration details to .env file")
```

### Common Issues

| Issue | Solution |
|-------|----------|
| **401 Unauthorized** | Check token validity or refresh integration credentials |
| **403 Forbidden** | Verify admin permissions and recording scopes |
| **429 Rate Limited** | Application automatically waits and retries |
| **CSV Not Found** | Run Step 1 first to generate recording metadata |
| **Download Failures** | Check disk space and network connectivity |

### Debug Information

Enable detailed logging by uncommenting debug lines:

```python
# Uncomment for detailed API debugging
print("URL: " + url)
print("Headers: " + str(headers))  
print("Response: " + str(response.text))
```

## 🧪 Testing

### Manual Testing Steps

1. **Token Validation:**
   ```bash
   # Test with personal access token
   python recordings.py
   # Enter token when prompted
   ```

2. **Metadata Collection:**
   ```bash
   # Test recording discovery
   python recordings.py
   > 1
   > demo.webex.com
   > 1
   ```

3. **Download Process:**
   ```bash
   # Test file downloads
   python recordings.py
   > 2
   ```

### Integration Testing

```python
# Test OAuth token refresh
export client_id="your_client_id"
export client_secret="your_client_secret"  
export refresh_token="your_refresh_token"
python recordings.py
```

## 📈 Performance Considerations

### Pagination Handling

The application processes recordings in batches of 100:

```python
url = "https://webexapis.com/v1/admin/recordings?siteUrl={0}&max=100&from={1}&to={2}"
# Handles pagination links automatically
while response.headers.get("link") is not None:
    url = response.headers.get("link").strip()[1:].split(">")[0]
```

### Time Range Strategy

Queries are split into 30-day chunks to manage large datasets:

```python
to_time = datetime.datetime.now().replace(microsecond=0)
from_time = to_time - datetime.timedelta(days=30)
# Process in 30-day increments until end_time reached
```

### Download Optimization

- Files are downloaded sequentially to avoid overwhelming the API
- Rate limiting is respected with automatic retry delays
- Large files are handled with streaming downloads

## 🔒 Security Considerations

### Token Security

- Store refresh tokens securely in `.env` file
- Add `.env` to `.gitignore` to prevent accidental commits
- Rotate tokens regularly following OAuth best practices

### File Handling

- Downloads are saved to dedicated directory (`Downloaded-Recordings/`)
- Original filenames are preserved from Webex metadata
- Ensure adequate disk space for large recording collections

### Access Control

- Requires admin-level permissions for recording access
- Respects organizational recording policies
- Maintains audit trail in CSV format

## 🤝 Contributing

We truly appreciate your contribution to the Webex Samples!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/download-enhancement`
3. Commit changes: `git commit -am 'Add download feature'`
4. Push to branch: `git push origin feature/download-enhancement`
5. Submit a Pull Request

### Development Guidelines

- Follow PEP 8 Python style guidelines
- Add error handling for new API calls
- Test with both personal tokens and OAuth integrations
- Update documentation for new features
- Consider rate limiting in any new API interactions

## 📄 License

This project is licensed under the Cisco Sample Code License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

For technical support and questions:

- **Issues**: Submit via GitHub Issues
- **API Documentation**: [Webex Recordings API](https://developer.webex.com/docs/api/v1/recordings)
- **OAuth Documentation**: [Webex OAuth Guide](https://developer.webex.com/docs/oauth)
- **Community**: [Webex Developer Community](https://developer.webex.com/community)

## Thanks!

Made with ❤️ by the Webex Developer Relations Team at Cisco

---

**Note**: This application is designed for administrative use with proper recording access permissions. Ensure compliance with your organization's data retention and privacy policies when downloading recordings.
