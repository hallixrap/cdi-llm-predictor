# **Azure Function and FHIR API Tutorial**

## **Step 1: Ensure VPN Connectivity**

* Connect to the **SHC VPN** to maintain security and prevent data leakage.

---

## **Step 2: Prerequisites**

Before you can deploy, you must have:

1. **Stanford Health Care SID** (Stanford ID)

2. Access to necessary SHC resources (Azure Functions, Databricks, etc.)

---

### **2.1 Obtain Your SID**

Follow these steps:

1. **Submit an Onboarding Request**

   * Visit: [SHC Onboarding Portal](https://stanfordhc.service-now.com/esc?id=sc_cat_item_guide&table=sc_cat_item&sys_id=c5b8d6e3b84f310060bc9bb4073b0bf2)

   * Request sponsorship from your department.

2. **Provide Required Details to Your Sponsor**

   * Job Title

   * Start & Termination Dates

   * Office Address, Room/Desk Number

   * Supervisor Name & Email

   * Director of Finance and Administration (DFA)

   * Department

3. **Supervisor Confirmation**

   * Your supervisor will receive an email to confirm your employment.

4. **Receive SID**

   * SID will be sent to your email once approved.

5. **Reset Password & Set Up Access**

   * Call **650-723-3333** for:

     * Password reset

     * Duo Authentication setup

     * VPN connectivity support

6. **Request SHC Account Access**

   * Ask **TDS** for a `stanford.health.org` account and **EPIC access**.

---

### **2.2 Request SHC Cloud Resources**

You need the following to deploy models:

#### **A. Azure Function Access**

* Azure Function is a **serverless, event-driven compute service** for model inference.

* Send an email to **Soumya Santhosh Punnathanam** (SHC):  
   **Email:** SPunnathanam@stanfordhealthcare.org  
   **Example Request:**

```
Could you please assist in granting access to the Azure Function, Databricks workspace, and other resources required for model deployment with the same level of access as Fatemeh and Yixing? Thank you!
```

#### **B. Databricks Workspace??**

* Request a Databricks workspace for **batch inference** from the SHC team.

---

### **2.3 Receive Credentials**

Once approved, SHC will provide:

* Azure login credentials (using SID)

* Read/Write access to **Cosmos DB**

* EPIC Client ID and environment details

* Databricks endpoint and access token

---

## **Step 3: Verify Azure Access**

* **Azure Portal:** [https://portal.azure.com/](https://portal.azure.com/) (Login with SID)

* **Visual Studio:**

  * Install **Azure Extension**

  * Sign in using your SID

---

## **Step 4: Local Testing of FHIR API**

To validate FHIR API access and functionality locally:

1. **Prepare the Files**

   * `requirements.txt`

   * `env_vars.sh` (contains credentials)

   * `test_fhir_epic.py`

2. **Install Dependencies**

```shell
pip install -r requirements.txt
```

3. **Set Environment Variables**

```shell

source env_vars.sh
```

4. This exports necessary credentials and environment variables.

5. **Run the Test Script**

```shell
python test_fhir_epic.py
```

   * The script will return **patient demographics** for the given **MRN (Medical Record Number)**.

