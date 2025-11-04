# GitHub Pre-Push Checklist ✅

## Before Pushing to GitHub

Run through this checklist to ensure safety:

---

## 1. Security Check ✅

### No Hardcoded Secrets
```bash
cd /Users/chukanya/Documents/Coding/New_CDI

# Check for hardcoded API keys
grep -r "ghp_\|5f57674c\|API.*=.*['\"]" --include="*.py" . --exclude-dir=_ARCHIVE_OLD_FILES
# Should return nothing or only comments/docs

# Check for hardcoded passwords
grep -r "password.*=.*['\"][^$]" --include="*.py" . --exclude-dir=_ARCHIVE_OLD_FILES | grep -v "example\|placeholder"
# Should return nothing
```

**Result**: ✅ All secrets use environment variables

---

## 2. PHI Check ✅

### No Patient Identifiers
```bash
# Check for real MRNs (Stanford MRNs are 7-8 digits, not starting with JC)
grep -rE "[^J][^C][0-9]{7,8}" --include="*.csv" data/CDI\ Sample.csv results/
# Should only show JC IDs (anonymized)

# Check for patient names
grep -ri "patient.*name\|first.*name\|last.*name" --include="*.csv" data/CDI\ Sample.csv results/ | head -5
# Should show column headers only, no actual names
```

**Result**: ✅ Only anonymized JC IDs in tracked files

---

## 3. Data Protection Check ✅

### Verify .gitignore is Working
```bash
cd /Users/chukanya/Documents/Coding/New_CDI

# Initialize git (if not done)
git init

# Check what would be committed
git status --porcelain

# Should NOT show:
# - env_vars.sh
# - tds_ds_env_vars.sh
# - data/raw/*.csv
# - data/processed/*.csv
# - _ARCHIVE_OLD_FILES/
```

**Expected Output**:
```
?? CLEANUP_PLAN.md
?? CLEANUP_SUMMARY.md
?? GITHUB_PRE_PUSH_CHECKLIST.md
?? GAP_ANALYSIS_RESULTS.md
?? README.md
?? requirements.txt
?? data/CDI Sample.csv
?? results/cdi_gap_analysis.csv
?? results/new_cdi_queries_evaluation.csv
?? scripts/analyze_cdi_gap_analysis.py
?? scripts/cdi_llm_predictor.py
?? scripts/evaluate_on_new_cdi_queries.py
?? web_demo/
```

**Should NOT See**:
- ❌ `env_vars.sh`
- ❌ `data/raw/` files
- ❌ `data/processed/` files
- ❌ `_ARCHIVE_OLD_FILES/`

**Result**: ✅ .gitignore is protecting sensitive files

---

## 4. File Count Check ✅

```bash
# Count tracked files
find . -type f \( -name "*.py" -o -name "*.md" -o -name "*.csv" -o -name "*.html" \) ! -path "./_ARCHIVE_OLD_FILES/*" ! -path "./data/raw/*" ! -path "./data/processed/*" ! -path "./.git/*" | wc -l
```

**Expected**: ~14-15 files

**Result**: ✅ 14 essential files

---

## 5. Dependencies Check ✅

```bash
# Verify requirements.txt exists
cat requirements.txt

# Expected output:
# flask>=3.0.0
# pandas>=2.0.0
# requests>=2.28.0
```

**Result**: ✅ All dependencies documented

---

## 6. Documentation Check ✅

### Required Files Present
- [ ] ✅ `README.md` - Main documentation
- [ ] ✅ `GAP_ANALYSIS_RESULTS.md` - Key findings
- [ ] ✅ `requirements.txt` - Dependencies
- [ ] ✅ `web_demo/README.md` - Demo instructions
- [ ] ✅ `web_demo/DEPLOYMENT_GUIDE.md` - Hosting guide

**Result**: ✅ All documentation complete

---

## 7. Functionality Check ✅

### Test Core Components
```bash
cd /Users/chukanya/Documents/Coding/New_CDI

# 1. Test imports
python3 -c "from scripts.cdi_llm_predictor import predict_missed_diagnoses; print('✅ Imports work')"

# 2. Test web demo (should already be running)
curl -s http://localhost:5001 | grep -q "CDI LLM Predictor" && echo "✅ Web demo works"

# 3. Check web demo files
ls web_demo/templates/index.html web_demo/app.py
```

**Result**: ✅ All components functional

---

## 8. Repository Setup Check ✅

### GitHub Repository Created
- [ ] Created repository: `cdi-llm-predictor`
- [ ] Set to **Private** ✅
- [ ] Did NOT initialize with README (using existing)

**Ready**: ✅

---

## 9. Final Security Scan ✅

### Run Comprehensive Check
```bash
cd /Users/chukanya/Documents/Coding/New_CDI

# Check for common secret patterns
echo "Scanning for secrets..."
grep -rE "(api[_-]?key|secret|password|token|credential).*=.*['\"][^$][^{]" \
  --include="*.py" --include="*.sh" --include="*.env*" \
  scripts/ web_demo/ . \
  --exclude-dir=_ARCHIVE_OLD_FILES \
  | grep -v "example\|placeholder\|TODO\|comment\|environ.get\|api_key_here" \
  | head -10 \
  || echo "✅ No secrets found"

# Check for Stanford-specific patterns
grep -rE "stanford|shc|epic" \
  --include="*.py" \
  scripts/ web_demo/ \
  | grep -iE "password|secret|key.*=.*['\"]" \
  || echo "✅ No Stanford secrets found"
```

**Result**: ✅ No hardcoded secrets detected

---

## 10. Push Checklist ✅

Before running `git push`:

- [ ] ✅ No PHI in tracked files
- [ ] ✅ No API keys/secrets in code
- [ ] ✅ .gitignore protecting sensitive files
- [ ] ✅ Only 14 essential files tracked
- [ ] ✅ Documentation complete
- [ ] ✅ Web demo functional
- [ ] ✅ Repository is **Private**
- [ ] ✅ Using Personal Access Token (not password)

---

## Ready to Push! 🚀

All checks passed. Safe to push to GitHub:

```bash
cd /Users/chukanya/Documents/Coding/New_CDI

# If not already initialized:
git init
git add .
git commit -m "Initial commit: CDI LLM Predictor with web demo"

# Connect to GitHub (replace 'your-username')
git remote add origin https://github.com/your-username/cdi-llm-predictor.git
git branch -M main

# Push to GitHub
git push -u origin main
```

Or use the automated script:
```bash
cd /Users/chukanya/Documents/Coding
./setup_github.sh your-username
```

---

## After Push Verification

Once pushed, verify on GitHub:

1. Go to: https://github.com/your-username/cdi-llm-predictor
2. Check repository is **Private** 🔒
3. Verify these files are present:
   - ✅ README.md
   - ✅ scripts/cdi_llm_predictor.py
   - ✅ web_demo/app.py
4. Verify these are NOT present:
   - ❌ env_vars.sh
   - ❌ data/raw/
   - ❌ _ARCHIVE_OLD_FILES/

---

## Emergency: If Secrets Were Pushed

If you accidentally pushed secrets:

1. **Immediately rotate all credentials**:
   - Contact Fateme Nateghi for new STANFORD_API_KEY
   - Contact François/Ruoqi for new FHIR credentials

2. **Remove from git history**:
   ```bash
   # Undo last commit (if just pushed)
   git reset --soft HEAD~1
   git restore --staged env_vars.sh
   git commit -m "Clean commit without secrets"
   git push --force
   ```

3. **For older commits**, use BFG Repo Cleaner or contact me for help

---

## Support

If any checks fail:
- Review the [CLEANUP_SUMMARY.md](CLEANUP_SUMMARY.md)
- Check [GITHUB_SETUP_GUIDE.md](/Users/chukanya/Documents/Coding/GITHUB_SETUP_GUIDE.md)
- Verify .gitignore is correct
