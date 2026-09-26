# 📊 GSC Intelligence Platform - SEMAI

**Advanced SEO, GEO & AEO Analytics Powered by SEMAI AI**

A comprehensive Google Search Console analytics platform that generates AI-powered audit reports for SEO optimization, including Deep Audit, Cluster Analysis, and Period Comparison reports.

---

## 🌟 Features

### 🔐 **Multi-User Authentication**
- Google OAuth 2.0 integration
- Persistent credential storage per user
- Automatic token refresh
- Support for multiple GSC properties

### 📊 **Three Report Types**

#### 1. Deep Audit Report
- Comprehensive SEO/GEO/AEO analysis
- 15+ analysis sections
- Obvious + Non-obvious insights (40% minimum non-obvious)
- CTR optimization strategies
- Content gap identification
- Internal linking recommendations

#### 2. Cluster Audit Report
- Cluster-based performance analysis
- Actionable micro-level recommendations
- AEO scorecard per cluster
- Citation hook strategies
- 7-day execution plan

#### 3. Period Comparison Report
- Compare two time periods
- Metrics delta analysis
- Top gainers/losers identification
- New and lost queries detection
- Strategic recommendations

### 🎨 **Professional UI**
- Modern gradient design (purple to violet theme)
- Responsive layout
- Clean navigation
- Real-time data extraction feedback

### 📥 **Export Options**
- **Word Documents (.docx)**: Professionally formatted reports with headings, tables, and styling
- **Markdown (.md)**: Raw markdown format
- **JSON**: Raw GSC data export
- **Excel (.xlsx)**: Multi-sheet workbooks with queries, pages, and metrics

### GA4 BigQuery Event Export
- Automatically detects a BigQuery link for the selected GA4 property
- Reads every completed daily event table in the selected date range
- Reconstructs sessions, key events, page performance, and user summaries
- Adds each transformed dataset to the GA4 Excel download
- Falls back to the complete paginated GA4 Data API extraction when BigQuery is unavailable

### 🔧 **Admin Dashboard**
- Separate `/admin` route
- Password-protected access
- View extracted GSC data
- Monitor queries, pages, and metrics
- Download raw data (JSON/Excel)

---

## 🚀 Installation

### Prerequisites
- Python 3.12+
- Google Cloud Project with Search Console API enabled
- Google OAuth 2.0 credentials

### Step 1: Clone Repository
```bash
git clone <repository-url>
cd v1
```

### Step 2: Install Dependencies
```bash
# Using conda (recommended)
conda create -n gsc_platform python=3.12
conda activate gsc_platform

# Install packages
pip install streamlit pandas python-docx openpyxl
pip install google-auth-oauthlib google-api-python-client
pip install google-generativeai
```

### Step 3: Setup Google OAuth 2.0
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable **Google Search Console API**
4. Create OAuth 2.0 credentials
5. Download `client_secret.json` and place in project root
6. Add authorized redirect URI: `http://localhost:8501`

### Step 4: Setup Gemini API
1. Get API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Set environment variable:
```bash
# Windows
set GEMINI_API_KEY=your_api_key_here

# Linux/Mac
export GEMINI_API_KEY=your_api_key_here
```

### Step 5: Configure Admin Password (Optional)
```bash
# Windows
set ADMIN_PASSWORD=your_secure_password

# Linux/Mac
export ADMIN_PASSWORD=your_secure_password
```

Default password: `semai2026` (change in production!)

---

## ☁️ Deploy on Streamlit Cloud (No Secrets in GitHub)

Use this flow when you want deployment without pushing tokens or credential files.

### 1. Push code safely
- Ensure `.gitignore` includes `.env`, `client_secret.json`, and `tokens/`.
- Push only application code to GitHub.

### 2. Create Streamlit app
- In Streamlit Cloud, create a new app from your GitHub repo.
- Set entrypoint to `app.py`.

### 3. Add Streamlit Secrets
- Open app settings -> **Secrets**.
- Copy values from `.streamlit/secrets.toml.example`.
- Set at minimum:
   - `GOOGLE_GEMINI_KEY`
   - `REDIRECT_URI` (your Streamlit app URL)
   - `GOOGLE_OAUTH_CLIENT_ID`
   - `GOOGLE_OAUTH_CLIENT_SECRET`
   - `ENABLE_TOKEN_PERSISTENCE = "false"`
- For OpenRouter with automatic key rotation, also set:
   - `OPENROUTER_API_KEY`
   - `OPENROUTER_API_KEY_2` (optional secondary key)

### 4. Configure Google OAuth correctly
- In Google Cloud Console -> OAuth 2.0 Client:
   - Add authorized redirect URI equal to your deployed URL (same as `REDIRECT_URI`).
   - Example: `https://your-app-name.streamlit.app`
- Enable required APIs:
   - Google Search Console API
   - Google Analytics Data API
   - Google Analytics Admin API
       - BigQuery API

### 5. Deploy and test
- Redeploy the app.
- Click **Sign in with Google**.
- Grant requested scopes.
- Run a Deep Audit report to verify LLM + Google APIs.

### GA4 BigQuery permissions

For event-level GA4 extraction, link the property under **GA4 Admin > Product
Links > BigQuery Links** and enable Daily Export. Each signed-in user must have
these IAM roles on the linked Google Cloud project:

- **BigQuery Data Viewer** on the `analytics_<property_id>` dataset
- **BigQuery Job User** on the linked project

After adding these permissions or upgrading from an older application version,
sign out and sign in again to grant the BigQuery OAuth scope. BigQuery export
does not backfill dates from before the GA4-to-BigQuery link was created; the
application continues to use the Data API for aggregate coverage of those dates.

### Notes for cloud token behavior
- Streamlit Cloud filesystem is ephemeral.
- This app supports `ENABLE_TOKEN_PERSISTENCE = "false"` to avoid file-based token issues.
- With `ENABLE_TOKEN_PERSISTENCE = "false"`, tokens are stored in memory during app uptime
   and reset when the app restarts.

---

## 📂 Project Structure

```
v1/
├── app.py                      # Main Streamlit application
├── deep_audit_prompt.py        # Deep audit AI prompt
├── cluster_audit_prompt.py     # Cluster audit AI prompt
├── comparison_prompt.py        # Comparison report AI prompt
├── client_secret.json          # Google OAuth credentials (not in repo)
├── tokens/                     # User credential storage
│   └── token_*.pickle          # Individual user tokens
├── README.md                   # This file
└── requirements.txt            # Python dependencies (optional)
```

---

## 🎯 Usage

### Starting the Application
```bash
streamlit run app.py
```

Application will open at: `http://localhost:8501`

### First Time Login
1. Click **"Sign in with Google"**
2. Authorize access to:
   - Google Search Console (read-only)
   - User email
3. Credentials will be saved for future sessions

### Generating Reports

#### **Single Period Analysis**
1. Select GSC property from dropdown
2. Choose date range (start and end dates)
3. Click report type:
   - **Deep Audit Report**: Comprehensive analysis
   - **Cluster Audit Report**: Cluster-based insights

#### **Period Comparison**
1. Enable "Period Comparison" checkbox
2. Select two date ranges:
   - Period 1 (baseline)
   - Period 2 (comparison)
3. Click **"Generate Period Comparison Report"**

### Accessing Admin Dashboard
1. Click **"🔐 Admin Dashboard"** in sidebar, OR
2. Navigate to: `http://localhost:8501/?page=admin`
3. Enter admin password (default: `semai2026`)
4. View extracted data, metrics, and download exports

---

## 🔑 Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | ✅ Yes | - | Google Gemini API key for AI analysis |
| `ADMIN_PASSWORD` | ❌ No | `semai2026` | Admin dashboard password |

---

## 🛠 Configuration

### Changing Admin Password
Edit in `app.py`:
```python
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "your_new_password")
```

### Customizing Prompts
Edit prompt files to customize AI analysis:
- `deep_audit_prompt.py`: Modify analysis depth and sections
- `cluster_audit_prompt.py`: Adjust cluster analysis criteria
- `comparison_prompt.py`: Change comparison metrics focus

### Adjusting AI Model Settings
In `app.py`, modify:
```python
MODEL = genai.GenerativeModel(
    model_name="gemini-3-flash-preview",
    generation_config={
        "temperature": 0.2,          # Lower = more focused
        "max_output_tokens": 8192    # Increase for longer reports
    }
)
```

---

## 📊 Report Features

### Deep Audit Report Includes:
- Executive Summary
- Data Sanity Checks
- Top 20 Queries/Pages
- Brand vs Non-brand Segmentation
- Position Bucket Analysis
- Root Cause Diagnosis (12+ insights)
- CTR Optimization Opportunities
- Content Gap Analysis (45+ page recommendations)
- 30-60-90 Day Execution Plan
- AEO/GEO Execution Layer

### Cluster Audit Report Includes:
- Cluster Summary (8-15 clusters)
- AEO Scorecard
- Page Fix Briefs
- Follow-up Query Chains
- Citation Hook Builder
- 7-Day Action Plan
- Trust & Depth Analysis

### Comparison Report Includes:
- Overall Metrics Comparison
- Top Gainers/Losers
- New and Lost Queries
- Page Performance Changes
- Root Cause Hypotheses
- Strategic Recommendations
- Opportunities & Risks

---

## 🔒 Security Notes

### For Production Deployment:
1. **Change default admin password**
2. **Use environment variables** for sensitive data
3. **Enable HTTPS** if deploying publicly
4. **Restrict OAuth redirect URIs** to production domain
5. **Set proper file permissions** for `tokens/` directory
6. **Use secrets management** (e.g., Streamlit Secrets, AWS Secrets Manager)

### Token Storage:
- User tokens stored in `tokens/` directory
- Encrypted via pickle serialization
- One token file per user
- Automatic cleanup on logout

---

## 🐛 Troubleshooting

### "Authentication failed" Error
- Verify `client_secret.json` is in project root
- Check OAuth redirect URI matches: `http://localhost:8501`
- Ensure user has granted email permissions

### "No GSC properties found"
- Verify user has access to GSC properties
- Check GSC API is enabled in Google Cloud Console
- User must have at least "Restricted" access level

### "Import Error: No module named 'docx'"
```bash
pip install python-docx
```

### "GEMINI_API_KEY not set"
```bash
# Windows
set GEMINI_API_KEY=your_key

# Linux/Mac
export GEMINI_API_KEY=your_key
```

### Admin Dashboard Shows "No Data"
- Generate at least one report first
- Data is stored in session state
- Check admin password is correct

---

## 🚢 Deployment

### Streamlit Cloud
1. Push code to GitHub
2. Deploy on [Streamlit Cloud](https://streamlit.io/cloud)
3. Add secrets in dashboard:
   ```toml
   GEMINI_API_KEY = "your_key"
   ADMIN_PASSWORD = "your_password"
   ```
4. Upload `client_secret.json` via secrets

### Docker
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["streamlit", "run", "app.py", "--server.port=8501"]
```

### Heroku/AWS/GCP
- Set environment variables in platform settings
- Ensure OAuth redirect URI matches deployed URL
- Use persistent storage for `tokens/` directory

---

## 📈 Tech Stack

- **Framework**: Streamlit 2026+
- **AI Model**: Google Gemini 3 Flash Preview
- **APIs**: 
  - Google Search Console API v1
  - Google OAuth2 API v2
  - Google Generative AI API
- **Data Processing**: Pandas
- **Document Generation**: python-docx, openpyxl
- **Authentication**: Google OAuth 2.0
- **Storage**: Pickle (credential serialization)

---

## 📝 License

Proprietary - SEMAI © 2026

---

## 👥 Support

For issues or questions:
- **Email**: support@semai.ai
- **Documentation**: [Internal Wiki]
- **Admin**: Contact SEMAI development team

---

## 🎯 Roadmap

- [ ] Scheduled report generation
- [ ] Email report delivery
- [ ] Historical data comparison
- [ ] Custom metric tracking
- [ ] Team collaboration features
- [ ] API access for integrations

---
