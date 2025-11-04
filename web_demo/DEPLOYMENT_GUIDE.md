# Deployment Guide - CDI LLM Predictor Web Demo

## Quick Share for Demo (Option 1: ngrok)

### Setup (5 minutes)
```bash
# Install ngrok
brew install ngrok

# Sign up for free account at https://ngrok.com
# Get your auth token from dashboard

# Add auth token
ngrok config add-authtoken YOUR_AUTH_TOKEN_HERE

# Start ngrok tunnel (from New_CDI directory)
ngrok http 5001
```

### Usage
1. Make sure your Flask app is running on localhost:5001
2. Run `ngrok http 5001` in a separate terminal
3. Copy the public URL (e.g., `https://abc123.ngrok-free.app`)
4. Share that URL with your boss - they can access it from anywhere!

### Pros
- ✅ Public URL in 30 seconds
- ✅ Works from anywhere (doesn't need Stanford VPN for viewers)
- ✅ Free tier available
- ✅ Perfect for demos

### Cons
- ❌ URL changes every time you restart (unless paid tier)
- ❌ Session expires after inactivity
- ⚠️ Note: Stanford VPN still required for API calls to work

---

## Stanford Internal Hosting (Option 2: Recommended for Production)

### Contact
- **Fateme Nateghi** (your API key contact)
- **Stanford Healthcare IT**

### Ask About
1. Internal Flask app hosting options
2. Clinical tools deployment process
3. PHI-safe hosting infrastructure
4. Domain name (e.g., `cdi-predictor.stanfordhealthcare.org`)

### Benefits
- ✅ Behind Stanford firewall
- ✅ PHI-safe by default
- ✅ IT handles security, backups, SSL
- ✅ Can use Stanford domain
- ✅ No personal cloud costs

### Process
1. Submit IT ticket or contact Fateme
2. Provide:
   - App description: "CDI documentation integrity assistant"
   - Tech stack: Python Flask web app
   - Security: Uses Stanford PHI-safe API
   - Users: Stanford CDI specialists and physicians
3. IT will likely provide:
   - VM or Kubernetes deployment
   - SSL certificate
   - Monitoring

---

## Cloud Hosting (Option 3: If Stanford Doesn't Host)

### 3a. Heroku (Easiest Cloud)

**Setup:**
```bash
# Install Heroku CLI
brew install heroku

# Login
heroku login

# Create app
cd /Users/chukanya/Documents/Coding/New_CDI
heroku create cdi-predictor-stanford

# Deploy
git push heroku main

# Set environment variable
heroku config:set STANFORD_API_KEY=your_key_here
```

**URL**: `https://cdi-predictor-stanford.herokuapp.com`

**Cost**:
- Free tier: $0 (sleeps after 30 min)
- Hobby: $7/month (always on)

**Note**: For PHI data, need HIPAA BAA (paid tier only)

---

### 3b. Google Cloud Run (Stanford's Cloud)

Stanford uses Google Cloud, so you may get credits.

**Setup:**
```bash
# Install Google Cloud CLI
brew install google-cloud-sdk

# Login to Google Cloud
gcloud auth login

# Create project or use Stanford's
gcloud config set project stanford-healthcare-project

# Build and deploy
gcloud run deploy cdi-predictor \
  --source . \
  --platform managed \
  --region us-west1 \
  --allow-unauthenticated
```

**Cost**: Pay per request (~$0 for low traffic)

---

### 3c. AWS (Enterprise Option)

Most robust but most complex.

**Services needed**:
- Elastic Beanstalk (easy) or EC2 (manual)
- RDS (if you add database later)
- CloudFront (CDN)
- Route 53 (DNS)

**Cost**: ~$20-50/month for small instance

---

## Deployment Checklist

### Before Going Live
- [ ] Remove debug mode: `app.run(debug=False)`
- [ ] Use production WSGI server (gunicorn instead of Flask dev server)
- [ ] Set up HTTPS/SSL
- [ ] Add authentication (if needed)
- [ ] Rate limiting (prevent abuse)
- [ ] Logging and monitoring
- [ ] Error handling
- [ ] Security review for PHI data

### Production-Ready Changes Needed

**1. Install gunicorn:**
```bash
pip install gunicorn
```

**2. Create Procfile (for Heroku):**
```
web: gunicorn --chdir web_demo app:app
```

**3. Create requirements.txt:**
```bash
pip freeze > requirements.txt
```

**4. Update app.py:**
```python
if __name__ == '__main__':
    # For local development only
    app.run(debug=True, host='127.0.0.1', port=5001)
```

**5. Add .env file for secrets:**
```
STANFORD_API_KEY=your_key_here
FLASK_ENV=production
```

---

## My Recommendation

### For This Week (Demo):
**Use ngrok** - Get public URL in 5 minutes

### For Next Month (Production):
**Contact Stanford IT** - Get proper hosting for PHI data

### Steps:
1. **Today**: Demo with ngrok to your boss
2. **This week**: Get boss approval for hosting
3. **Next week**: Contact Stanford IT about hosting options
4. **Following week**: Deploy to Stanford infrastructure

---

## Need Help?

**For ngrok setup**: Run these commands:
```bash
brew install ngrok
ngrok config add-authtoken YOUR_TOKEN
ngrok http 5001
```

**For Stanford IT**: Contact Fateme Nateghi or submit IT ticket

**For cloud deployment**: I can help set up any of the cloud options
