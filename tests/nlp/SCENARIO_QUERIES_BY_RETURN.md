# Scenario-wise test queries, per return (English)

Every return, table, column and row label here is taken from the real embedding
index (`artifacts/nlp-index/ideal-55`, `ideal-60`). The expected column says what the
module must DO; the numbers depend on the data in Oracle, so check them in the
drill-down ("Show details of the first one") or the **Source** panel.

**How to read a row:** "S2" = scenario group 2 from the requirement list. Queries that
end in "vs previous quarter" skip the "which date?" question; for any other query
without a date, answer that question with **Skip** (latest vs previous).

**Before each section:** press **New question** (or reload) so the follow-up context
from the previous section does not carry over.

Contents: [CIMS_RAQ](#1-cims_raq-quarterly--ideal-55) ·
[CIMS_ALE Domestic](#2-cims_ale_domestic-quarterly--ideal-55) ·
[CIMS_ALE Overseas](#3-cims_ale-oversease-quarterly--ideal-55) ·
[QCB_F010](#4-qcb_f010-maturity-ladder--ideal-60) ·
[QCB_F013](#5-qcb_f013-exposures-by-geography--ideal-60) ·
[QCB_F014](#6-qcb_f014-funding-by-geography--ideal-60) ·
[QCB_F015](#7-qcb_f015-foreign-currency-net-open-positions--ideal-60)

---

## 1. CIMS_RAQ (Quarterly) — iDEAL 5.5

Tables used: Industry breakdown (SEC8_IND_BRKUP; rows such as "B.1 Sugar", "D. Textiles",
"A.1 Coal", "All Industries (A to S)"), Delinquency (SEC1_PART_A_DOM; rows such as
"i) Current", "B. NON-PERFORMING"), Investments (SEC6_PART_A), Gross NPA (SEC_12_MISC_T4;
rows "Regulatory Retail", "Wholesale Advances", "Total Advances").

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 1 | Compare industry wise total exposure in CIMS_RAQ with previous quarter | All industries; summary "Compared N rows … increased … decreased …" |
| 2 | 1 | Gross advances in RAQ compared to last year | Gross NPA table; latest vs the same quarter a year earlier |
| 3 | 1 | Give me a summary of the changes in industry wise exposure in CIMS_RAQ vs previous quarter | Summary block: counts and the 5 largest increases / decreases |
| 4 | 2 | Which industries changed in total exposure in CIMS_RAQ vs previous quarter | Unchanged industries dropped |
| 5 | 2 | Show variation for element B.1 Sugar in industry wise exposure CIMS_RAQ vs previous quarter | Only the Sugar row |
| 6 | 2 | Has element D. Textiles increased or decreased in CIMS_RAQ industry exposure vs previous quarter | Only Textiles, with a status per column |
| 7 | 3 | Show me variation in the Total Exposure column for CIMS_RAQ industry breakdown vs previous quarter | Only the Total Exposure column |
| 8 | 3 | Compare Total Exposure, Substandard Advances and SMA-2 Advances columns in CIMS_RAQ industry breakdown vs previous quarter | Exactly those 3 columns |
| 9 | 3 | Which columns have changed in CIMS_RAQ industry breakdown vs previous quarter | Column summary lists only the columns that moved |
| 10 | 3 | Show variation for all monetary columns in CIMS_RAQ industry breakdown vs previous quarter | Every numeric column |
| 11 | 4 | Industries in CIMS_RAQ where total exposure changed by more than 10% vs previous quarter | \|% change\| > 10 |
| 12 | 4 | Show absolute variation in Term Loans by period of delinquency in CIMS_RAQ vs previous quarter | Measure = absolute |
| 13 | 5 | Show only decreased values in Total Loan Assets by period of delinquency in CIMS_RAQ vs previous quarter | Decreases only |
| 14 | 5 | Give me the top 10 increases in CIMS_RAQ industry exposure by percentage vs previous quarter | 10 rows, largest % first |
| 15 | 6 | Show changes above ₹10 crore in CIMS_RAQ industry exposure vs previous quarter | Absolute change > 100,000,000, with a note about the report's units |
| 16 | 6 | Show variations between 10% and 30% in CIMS_RAQ industry exposure vs previous quarter | 10 ≤ \|%\| ≤ 30 |
| 17 | 6 | Identify changes below -15% in CIMS_RAQ industry exposure vs previous quarter | % < -15 |
| 18 | 7 | Show top 5 industries with highest variation in CIMS_RAQ total exposure vs previous quarter | **Asks: absolute or %?** Then 5 rows |
| 19 | 7 | Give me the elements with the lowest variation in CIMS_RAQ industry exposure by percentage vs previous quarter | Up to 10 rows, smallest first |
| 20 | 8 | Which industries have no variation in CIMS_RAQ total exposure vs previous quarter | Unchanged only |
| 21 | 8 | Find columns with zero variation in CIMS_RAQ industry breakdown vs previous quarter | Only columns where every value is unchanged |
| 22 | 9 | Show newly populated elements in CIMS_RAQ industry exposure vs previous quarter | Previous 0 → now non-zero, with a "% undefined" note |
| 23 | 9 | Which elements became zero in CIMS_RAQ industry exposure vs previous quarter | Non-zero → 0 |
| 24 | 10 | Compare FY 2025-26 with FY 2024-25 industry wise exposure in CIMS_RAQ | Fiscal year Apr–Mar (5.5) |
| 25 | 10 | Show variance in Term Loans in CIMS_RAQ for Q3 | Q3 vs the previous quarter |
| 26 | 11 | Show the trend of Total Loan Assets by period of delinquency in CIMS_RAQ for the last 4 quarters | Trend table, 5 dates |
| 27 | 11 | Which delinquency buckets have continuously increased over the last 4 quarters in CIMS_RAQ Term Loans | Only series that rose at every step (may be empty, and says so) |
| 28 | 12 | Find unusual variations in CIMS_RAQ industry exposure vs previous quarter | Note starting "Rule: … quartiles ± 1.5 × IQR" |
| 29 | 15 | Sort by percentage variation for CIMS_RAQ industry exposure vs previous quarter | All rows, largest \|%\| first |
| 30 | 15 | Sort alphabetically by element for CIMS_RAQ industry exposure vs previous quarter | A→Z |
| 31 | 16 | For element D. Textiles, compare all columns in CIMS_RAQ industry breakdown vs previous quarter | 1 row, all columns |
| 32 | 17 | Which numbers require my attention in CIMS_RAQ industry exposure vs previous quarter | Anomaly answer, with its rule |
| 33 | 18 | Give me an executive summary of the variation in CIMS_RAQ industry exposure vs previous quarter | Summary block |
| 34 | 19 | Identify significant changes before submission in CIMS_RAQ industry exposure vs previous quarter | **Asks the threshold** |
| 35 | 22 | How is the percentage change calculated for CIMS_RAQ industry exposure vs previous quarter | Formula note |
| 36 | 14 | Show variation only for Treasury-related elements in CIMS_RAQ investments vs previous quarter | Rows whose label holds "Treasury" |
| 37 | 26 | Show highest variation in Gross NPA in CIMS_RAQ vs previous quarter | Asks absolute or % |
| 38 | 27 | Compare industry wise exposure in CIMS_RAQ with 2020 | **404** "Data is not available for 2020 … available from … to …" |
| 39 | 27 | Show me variation in the Goodwill column for CIMS_RAQ industry breakdown vs previous quarter | **404** column not found, with the available list |

**Follow-up chain (S13 / S23 / S24).** Type in order:

1. Compare industry wise total exposure in CIMS_RAQ with previous quarter
2. Show top 10 by percentage
3. Only decreases
4. More than 20%
5. Show details of the first one → drill-down table
6. Compare the same element for the last 6 quarters → trend for that industry
7. Now show absolute instead of percentage
8. Compare with previous year

Throughout, the Source panel keeps the same table. Step 8 changes the dates only.

---

## 2. CIMS_ALE_Domestic (Quarterly) — iDEAL 5.5

Always write "CIMS_ALE_Domestic" (or "ALE Domestic"); a plain "ALE" asks Domestic or
Overseas (that is test 1). Tables used: Assets Part-A (SEC_1A; Domestic / Overseas / Global
Operations totals in Rs. Lakh), Off balance sheet (SEC_2A; Book Value, Credit Equivalent),
Derivatives D1 (SEC2_D1; Notional Principal, Negative MTM value, VaR), Annex 1 (ANX_1_A;
Debit / Credit Entries Amount), Current-account balances (ANX_1_BAL; rows "With banks in India",
"With banks Outside India").

ALE rows have long labels that were not in the index samples. For the element tests,
copy a row label from the first result.

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 26 | Show liabilities in ALE | **Asks the return**: ALE Domestic / ALE Oversease |
| 2 | 1 | Compare assets in CIMS_ALE_Domestic with previous quarter | SEC_1A; all rows |
| 3 | 1 | What has changed in CIMS_ALE_Domestic assets between the current and previous reporting period | Changed rows only |
| 4 | 2 | Show variation for element <paste a row label> in CIMS_ALE_Domestic assets vs previous quarter | That row only |
| 5 | 3 | Show me variation in the Global Operations Total column for CIMS_ALE_Domestic assets vs previous quarter | 1 column |
| 6 | 3 | Compare Domestic Operations Book Value and Global Operations Book Value columns in CIMS_ALE_Domestic off balance sheet vs previous quarter | 2 columns |
| 7 | 4 | Show absolute variation in Debit Entries Amount for accounts with banks in India in CIMS_ALE_Domestic vs previous quarter | ANX_1_A, absolute |
| 8 | 5 | Show only increased values in Credit Entries Amount in CIMS_ALE_Domestic vs previous quarter | Increases |
| 9 | 6 | Show changes above 50 lakh in CIMS_ALE_Domestic assets vs previous quarter | Absolute change > 5,000,000 (values are in Rs. Lakh, see the note) |
| 10 | 6 | Show elements where variation is more than 25% in either direction in CIMS_ALE_Domestic off balance sheet exposures vs previous quarter | Both directions |
| 11 | 7 | Show top 5 derivatives by absolute change in Notional Principal Total in CIMS_ALE_Domestic vs previous quarter | 5 rows |
| 12 | 7 | Which column has the maximum increase in CIMS_ALE_Domestic assets vs previous quarter | 1 column, increase direction |
| 13 | 8 | Show elements with no change in CIMS_ALE_Domestic derivatives vs previous quarter | Unchanged |
| 14 | 9 | Which elements had values previously but are zero now in CIMS_ALE_Domestic derivatives vs previous quarter | Became zero |
| 15 | 10 | Compare CIMS_ALE_Domestic assets for 31-Mar-2025 with 31-Dec-2024 | Exactly those 2 dates |
| 16 | 11 | Show quarter-on-quarter variation in CIMS_ALE_Domestic assets for the last 4 quarters | Trend, QoQ |
| 17 | 12 | Identify potential outliers in CIMS_ALE_Domestic off balance sheet exposures vs previous quarter | Anomaly rule note |
| 18 | 15 | Sort by absolute difference for CIMS_ALE_Domestic assets vs previous quarter | All rows, largest \|change\| first |
| 19 | 16 | Which element-column combination has the highest variation in CIMS_ALE_Domestic assets vs previous quarter | Asks the measure, then 1 cell |
| 20 | 18 | Summarize increases and decreases separately in CIMS_ALE_Domestic assets vs previous quarter | Summary block with both lists |
| 21 | 20 | Are there any unexpected changes in CIMS_ALE_Domestic derivatives vs previous quarter | Anomaly |
| 22 | 22 | Show both absolute and percentage variation for balances in current accounts in CIMS_ALE_Domestic vs previous quarter | ANX_1_BAL, both shown |
| 23 | 26 | Show significant changes in CIMS_ALE_Domestic assets vs previous quarter | Asks the threshold; type "50 lakh" |
| 24 | 27 | Show variation in the Goodwill column for CIMS_ALE_Domestic assets vs previous quarter | 404 column not found |

**Follow-up chain.** Type in order:

1. Compare assets in CIMS_ALE_Domestic with previous quarter
2. Top 5 by absolute
3. Only increases
4. Show details of the second one
5. Now do the same analysis for off balance sheet exposures (switches to SEC_2A)

---

## 3. CIMS_ALE Oversease (Quarterly) — iDEAL 5.5

Same tables as Domestic (the index spells it "Oversease"). Name it explicitly.

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 1 | Compare assets in CIMS_ALE Oversease with previous quarter | The Overseas return is used (check the Return chip) |
| 2 | 3 | Show me variation in the Overseas Operations Total column for CIMS_ALE Oversease assets vs previous quarter | 1 column |
| 3 | 5 | Give me the top 10 decreases in CIMS_ALE Oversease assets by percentage vs previous quarter | 10 rows |
| 4 | 6 | Show variations greater than 20% in CIMS_ALE Oversease off balance sheet exposures vs previous quarter | \|%\| > 20 |
| 5 | 9 | Identify newly reported values in CIMS_ALE Oversease derivatives vs previous quarter | New from zero |
| 6 | 11 | Show the trend of Global Operations Total in CIMS_ALE Oversease assets for the last 4 quarters | Trend of that column |
| 7 | 18 | Give me a management-level view of the variation in CIMS_ALE Oversease assets vs previous quarter | Summary block |
| 8 | 26 | Credit default swap protection buyer | Asks the return (the table is in both ALE returns) |

---

## 4. QCB_F010 Maturity Ladder — iDEAL 6.0

Tables used: Inflows (RCM_IN_QTR) and Outflows (RCM_OUT_QTR). Rows such as "Cash and Cash
Equivalents", "Claims on Banks", "Claims on Central Banks", "Call/Overnight Loans …",
"Cumulative gap - on balance sheet". Columns per bucket and currency: "Overnight - LC",
"> 1 to 7 days - USD", "TOTAL - LC", "TOTAL - USD" …

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 1 | Compare inflows maturity ladder in QCB_F010 with previous quarter | Inflows table, all rows |
| 2 | 1 | Show outflows maturity ladder for QCB_F010 compared to same quarter last year | Outflows table, YoY |
| 3 | 2 | Show variation for element Cash and Cash Equivalents in QCB_F010 inflows vs previous quarter | 1 row, every bucket |
| 4 | 2 | Has element Claims on Banks increased or decreased in QCB_F010 inflows vs previous quarter | 1 row |
| 5 | 3 | Show inflows in overnight LC bucket in QCB_F010 vs previous quarter | Overnight - LC column |
| 6 | 3 | Compare TOTAL - LC and TOTAL - USD columns in QCB_F010 inflows vs previous quarter | 2 columns |
| 7 | 3 | Which maturity bucket column has the maximum increase in QCB_F010 inflows vs previous quarter | 1 column, increases only |
| 8 | 4 | Show percentage variation column-wise in QCB_F010 outflows vs previous quarter | Column summary by % |
| 9 | 5 | Which inflow lines decreased in TOTAL - LC in QCB_F010 vs previous quarter | Decreases |
| 10 | 6 | Show changes above 1 million in QCB_F010 inflows vs previous quarter | Absolute change > 1,000,000 |
| 11 | 7 | Show top 3 inflow lines by absolute change in TOTAL - LC in QCB_F010 vs previous quarter | 3 rows |
| 12 | 8 | Find columns with zero variation in QCB_F010 inflows vs previous quarter | Unused buckets (often the Other FC ones) |
| 13 | 9 | Which elements became zero in QCB_F010 outflows vs previous quarter | Became zero |
| 14 | 10 | Compare Q1 with Q4 in QCB_F010 inflows | Consecutive quarters (6.0 = calendar fiscal year) |
| 15 | 10 | Compare QCB_F010 inflows for December 2025 with September 2025 | Those 2 quarter ends |
| 16 | 11 | Show the trend of Cash and Cash Equivalents in QCB_F010 inflows for the last 4 quarters | Trend of that row |
| 17 | 12 | Show me the exceptions in QCB_F010 outflows vs previous quarter | Anomaly rule |
| 18 | 15 | Sort by current value for QCB_F010 inflows vs previous quarter | Largest current first |
| 19 | 16 | For element Claims on Central Banks, compare all columns in QCB_F010 inflows vs previous quarter | 1 row, all buckets |
| 20 | 17 | What are the key movements in QCB_F010 outflows vs previous quarter | Summary block |
| 21 | 26 | Compare cash inflows 1 to 7 days USD in F010 for Q3 | Asks detail (the inflow and outflow tables tie) |
| 22 | 27 | Compare QCB_F010 inflows with 2020 | 404 data not available |

**Follow-up chain.** Type in order:

1. Compare inflows maturity ladder in QCB_F010 with previous quarter
2. Show top 5 by absolute
3. Only decreases
4. Show details of the first one
5. Now do the same analysis for outflows

---

## 5. QCB_F013 Exposures by geography — iDEAL 6.0

Table BREAK_EXPOSURE. Rows: countries (Australia, China, France, India, Japan, Kuwait,
Korea (the Republic of), Total …). Columns: Sovereigns, Multilateral Development Banks,
Public Sector Entities, Banks, Private Sector - Large Corporates, Private Sector - SMEs,
Non-Bank Financial Institutions, Retail, Total.

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 1 | Compare exposures by geography in QCB_F013 with previous quarter | All countries |
| 2 | 1 | Exposure to SMEs by country in QCB_F013 current vs previous | Private Sector - SMEs |
| 3 | 2 | Show variation for element India in QCB_F013 exposures vs previous quarter | India only |
| 4 | 2 | Which countries have changed in QCB_F013 exposures vs previous quarter | Changed only |
| 5 | 3 | Exposure to sovereigns by country in QCB_F013 vs previous quarter | Sovereigns column |
| 6 | 3 | Compare Sovereigns, Banks and Retail columns in QCB_F013 vs previous quarter | 3 columns |
| 7 | 3 | Which column has the highest variation in QCB_F013 vs previous quarter | Asks the measure, then 1 column |
| 8 | 4 | Show countries where total exposure changed by more than 10% in QCB_F013 vs previous quarter | \|%\| > 10 |
| 9 | 5 | Which country experienced the largest decrease in total exposure in QCB_F013 vs previous quarter | 1 country |
| 10 | 5 | Give me the top 10 increases in QCB_F013 exposures by percentage vs previous quarter | 10 rows |
| 11 | 6 | Show countries where total exposure increased by more than 25% in QCB_F013 vs previous quarter | Increases > 25% |
| 12 | 6 | Show exposures with absolute variation greater than 5 million in QCB_F013 vs previous quarter | Absolute change > 5,000,000 |
| 13 | 8 | Which countries remained unchanged in QCB_F013 exposures vs previous quarter | Unchanged |
| 14 | 9 | Show newly reported countries in QCB_F013 exposures vs previous quarter | New from zero |
| 15 | 9 | Identify countries that disappeared from the current period in QCB_F013 exposures | Became zero |
| 16 | 10 | Compare FY 2025 with FY 2024 exposures in QCB_F013 | Calendar years |
| 17 | 11 | Show year-on-year variation of exposures by geography in QCB_F013 | Trend, YoY |
| 18 | 12 | Identify the top anomalies based on variation in QCB_F013 exposures vs previous quarter | Anomaly rule |
| 19 | 14 | Show total exposure in QCB_F013 for Mumbai branch vs previous quarter | Empty, with "No rows … have branch 'Mumbai'" (there is no branch column; this is correct) |
| 20 | 15 | Sort alphabetically by element for QCB_F013 exposures vs previous quarter | A→Z by country |
| 21 | 16 | For element Japan, compare all columns in QCB_F013 vs previous quarter | 1 row, all columns |
| 22 | 18 | Give me a concise summary of current vs previous in QCB_F013 exposures | Summary block (asks the date first) |
| 23 | 26 | Compare March in QCB_F013 exposures | **Asks the period**: previous or the same period last year |
| 24 | 26 | Breakdown by geography | Asks the return: F013 / F014 |
| 25 | 27 | Show variation for element Atlantis in QCB_F013 exposures vs previous quarter | "Element 'Atlantis' not found" |

**Follow-up chain.** Type in order:

1. Compare exposures by geography in QCB_F013 with previous quarter
2. Top 10 by percentage
3. Only decreases
4. More than 20%
5. Show details of the first one
6. Compare the same element for the last 4 quarters

---

## 6. QCB_F014 Funding by geography — iDEAL 6.0

Table FUNDING_GEO. Rows: countries (Algeria, Bangladesh, Canada, Germany, India, Kuwait …).
Same columns as F013.

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 1 | Compare funding by geography in QCB_F014 with previous quarter | All countries |
| 2 | 1 | Retail funding by country in QCB_F014 as of 31-Dec-2024 | 31-Dec-2024 vs the previous date |
| 3 | 2 | Show variation for element Germany in QCB_F014 funding vs previous quarter | Germany only |
| 4 | 3 | Funding from retail by country in QCB_F014 vs previous quarter | Retail column |
| 5 | 3 | Compare Banks and Private Sector - Large Corporates columns in QCB_F014 vs previous quarter | 2 columns |
| 6 | 4 | Which countries have changed by more than 10% in QCB_F014 funding vs previous quarter | \|%\| > 10 |
| 7 | 5 | Show only decreased values in QCB_F014 funding vs previous quarter | Decreases |
| 8 | 6 | Show variations between 10% and 30% in QCB_F014 funding vs previous quarter | Range |
| 9 | 7 | Show top 5 countries with highest variation in QCB_F014 funding vs previous quarter | Asks the measure, then 5 rows |
| 10 | 8 | Find columns with zero variation in QCB_F014 funding vs previous quarter | Fully unchanged columns |
| 11 | 9 | Which elements became zero in QCB_F014 funding vs previous quarter | Became zero |
| 12 | 10 | Compare retail funding by country in QCB_F014 with 2020 | **404** data not available |
| 13 | 11 | Show quarter-on-quarter variation of retail funding by country in QCB_F014 for the last 4 quarters | Trend, QoQ |
| 14 | 12 | Highlight unusual changes in QCB_F014 funding vs previous quarter | Anomaly rule |
| 15 | 15 | Show highest increases first in QCB_F014 funding vs previous quarter | Increases, biggest first |
| 16 | 17 | Where has the bank's funding changed the most in QCB_F014 vs previous quarter | Asks the measure, then a ranking |
| 17 | 18 | Give me the top 5 changes in QCB_F014 funding by absolute vs previous quarter | 5 rows |
| 18 | 26 | Show significant changes in QCB_F014 funding vs previous quarter | Asks the threshold |
| 19 | 26 | Show sovereigns by country | Asks the return: F013 / F014 |

---

## 7. QCB_F015 Foreign Currency Net Open Positions — iDEAL 6.0

Table FOREIGN_CUR_NOP. Rows: currencies (e.g. USD). Columns: Total assets, Total
liabilities, Customer Deposits - Total, Loans and advances to customers - Total, Due to
Banks - Total, Surplus/deficit, Exchange rate …

| # | S | Query | Expected |
|---|---|---|---|
| 1 | 1 | Compare foreign currency net open positions in QCB_F015 with previous quarter | All currencies |
| 2 | 2 | Show variance for USD net open position in QCB_F015 vs previous quarter | Currency rows (USD row if present) |
| 3 | 2 | Show variation for element USD in QCB_F015 vs previous quarter | USD only |
| 4 | 3 | Compare Total assets and Total liabilities columns in QCB_F015 vs previous quarter | 2 columns |
| 5 | 3 | Show me variation in the Surplus/deficit column for QCB_F015 vs previous quarter | 1 column |
| 6 | 4 | Show percentage variation in Customer Deposits - Total in QCB_F015 vs previous quarter | % |
| 7 | 5 | Which currencies decreased in Total assets in QCB_F015 vs previous quarter | Decreases |
| 8 | 6 | Show currencies where Surplus/deficit decreased by more than 10% in QCB_F015 vs previous quarter | Decreases beyond 10% |
| 9 | 7 | Which column has the largest decrease in QCB_F015 vs previous quarter | 1 column, decrease direction |
| 10 | 8 | Show unchanged values in QCB_F015 vs previous quarter | Unchanged (Exchange rate often) |
| 11 | 9 | Show elements where previous value was zero and current value is non-zero in QCB_F015 vs previous quarter | New from zero |
| 12 | 11 | Show the trend of Total assets in QCB_F015 for the last 4 quarters | Trend of that column |
| 13 | 12 | Identify abnormal changes in QCB_F015 vs previous quarter | Anomaly rule |
| 14 | 15 | Sort by previous value for QCB_F015 vs previous quarter | Largest previous first |
| 15 | 16 | For element USD, compare all columns in QCB_F015 vs previous quarter | 1 row, all columns |
| 16 | 18 | Summarize the major changes in QCB_F015 vs previous quarter | Asks the threshold ("major" = significant), then the summary |
| 17 | 22 | What is the absolute difference in Total liabilities in QCB_F015 vs previous quarter | Absolute |
| 18 | 26 | Show reporting currency | Asks the return: F010 / F013 / F014 / F015 |
| 19 | 27 | Show me total loans for QCB_F020 | Refused: F020 is not indexed |

---

## Every return: export (S25)
After any result, click **Export CSV**. The CSV header shows the summary, filters and
source, followed by one line per element × column. The values must match the screen.

## Out of scope (expected not to work)
- Hinglish prompts.
- S21: comparing two different returns.
- S14 branch / product / customer filters: none of these returns has such a column,
  so the answer is "No rows … have …".
