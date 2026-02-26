# Test User Data

Mock data used by the lost card agent. Source of truth: `server/app/agent/plugins/lost_card/tools.py`.

---

## Customer Profile

| Field | Value |
|---|---|
| Name | Sarah Mitchell |
| Card Last 4 | **4821** |
| Card Type | Barclays Visa Debit |
| Card Expiry | 09/27 |
| Registered Address | 14 Elmwood Close, Bristol, BS6 5AP |
| Phone | •••• •••• 7342 |
| Sort Code | 20-**-** |
| Account Number | ••••6714 |

> **Identity verification:** when prompted for the last 4 digits, enter `4821`.

---

## Suspicious Transactions (Fraud Flow)

| Date | Merchant | Amount | Flag |
|---|---|---|---|
| 26 Feb | GOOGLE*SVCS | -£149.99 | International charge — country: US |
| 25 Feb | AMAZON MKT EU | -£89.00 | Online purchase — unfamiliar seller |
| 24 Feb | WITHDRAWL — ATM UNKNOWN | -£200.00 | ATM not in usual area |

---

## Test Journeys

### Lost Card (happy path)
1. Open `http://localhost:3000/?agent=lost_card`
2. Say or type: `"I've lost my card"`
3. When prompted, provide last 4: **`4821`**
4. Card freezes — tap **Order Replacement Card** to continue

### Fraud Report
1. Say or type: `"I see suspicious transactions"`
2. Flagged transactions are displayed
3. Provide last 4 (**`4821`**) to freeze and escalate

### Found Card (recovery)
1. After a freeze, say: `"I found my card"`
2. Tap **Yes, Reactivate My Card** to unfreeze

### Wrong Digits (rejection)
1. When prompted for last 4, enter anything other than `4821`
2. Agent should reject and ask again
