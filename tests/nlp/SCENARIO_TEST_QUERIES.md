# Variation scenarios: manual test queries (groups 1–27, English)

**Where the names come from:** every return, table, column and row label below was
taken from the real embedding index (`artifacts/nlp-index/ideal-55` and
`ideal-60`: `schema.json` and `description_samples.json`).

**What is not covered:** the expected column describes what the module must *do*.
It cannot state the numbers, because those depend on the data in Oracle. Check each
number in the drill-down or the Source panel.

**Tables used**

| Host | Return | Table | Rows (label column) | Main numeric columns |
|---|---|---|---|---|
| 5.5 | CIMS_RAQ | CIMS_RAQ_Q_SEC8_IND_BRKUP | `industry_name`: "All Industries (A to S)", "B.1 Sugar", "D. Textiles", "A.1 Coal" … | Total Exposure, Total Funded Credit Exposure, Gross NPAs at end of the period, SMA-0/1/2 Advances |
| 5.5 | CIMS_RAQ | CIMS_RAQ_Q_SEC1_PART_A_DOM | `period_delinquency`: "i) Current", "A. PERFORMING(i + ii)", "B. NON-PERFORMING …", "C. Total ( A + B)" | Term Loans, Cash Credits/Overdrafts, Bills Purchased, Total Loan Assets |
| 5.5 | CIMS_RAQ | CIMS_RAQ_Q_SEC6_PART_A | `category_investment_dom_opr`: "GOI Treasury bills", "PSU bonds" … | Book Value Held to Maturity, Book Value Total, Market Value Total |
| 6.0 | QCB_F013 | QCB_F013_BREAK_EXPOSURE | `country`: India, Japan, China, France, Kuwait, Total … | Sovereigns, Banks, Private Sector – SMEs, Retail, Total |
| 6.0 | QCB_F014 | QCB_F014_FUNDING_GEO | `country`: India, Germany, Kuwait … | same columns as F013 |
| 6.0 | QCB_F010 | QCB_F010_RCM_IN_QTR | `description`: "Cash and Cash Equivalents", "Claims on Banks" … | Overnight – LC/USD, > 1 to 7 days – LC …, TOTAL – LC/USD |
| 6.0 | QCB_F015 | QCB_F015_FOREIGN_CUR_NOP | `currency` | Total assets, Total liabilities, Customer Deposits – Total, Surplus/deficit |

**Tip:** a query with no date first asks "which date?". Each query below either
names a period, or ends in "vs previous quarter" so the analysis runs straight
away. Without that, answer the date question with **Skip**.

---

## 5.5: CIMS_RAQ industry breakdown (SEC8)

### 1. Basic current vs previous
| Query | Expected |
|---|---|
| Compare industry wise total exposure in CIMS_RAQ with previous quarter | Every industry row is shown. Summary: "Compared N rows … X increased, Y decreased …". Source shows latest vs previous quarter |
| Industry wise total exposure in CIMS_RAQ compared to same quarter last year | Compares with the date 4 quarters back (YoY) |
| Give me a summary of the changes in industry wise exposure in CIMS_RAQ vs previous quarter | Summary detail block appears: headline counts, plus the 5 largest increases and the 5 largest decreases |
| Identify significant changes in industry wise exposure in CIMS_RAQ vs previous quarter | **Asks the threshold** (10 / 20 / 50% or typed). Skip = more than 10%, and a note says so |

### 2. Element-wise
| Query | Expected |
|---|---|
| Which industries changed in total exposure in CIMS_RAQ vs previous quarter | Unchanged industries are dropped |
| Show variation for element B.1 Sugar in industry wise exposure CIMS_RAQ vs previous quarter | Only the Sugar row |
| Has element D. Textiles increased or decreased in CIMS_RAQ industry exposure vs previous quarter | Only the Textiles row, with its status per column |
| Show variation for element Aviation in CIMS_RAQ industry exposure vs previous quarter | Empty, with "Element 'Aviation' not found". Use a name that is not in the list |

### 3. Column-wise
| Query | Expected |
|---|---|
| Show me variation in the Total Exposure column for CIMS_RAQ industry breakdown vs previous quarter | Only the Total Exposure column |
| Compare Total Exposure, Substandard Advances and SMA-2 Advances columns in CIMS_RAQ industry breakdown vs previous quarter | Exactly these 3 columns (a column name containing "of", "in" or "for", e.g. "Gross NPAs at end of the period", is cut at that word, so name it by its leading words) |
| Which column has the highest variation in CIMS_RAQ industry breakdown vs previous quarter | **Asks absolute or %**, then lists 1 column |
| Which columns have changed in CIMS_RAQ industry breakdown vs previous quarter | Column summary lists only the columns that moved |
| Show variation for all monetary columns in CIMS_RAQ industry breakdown vs previous quarter | Every numeric column (not just the resolver's pick) |
| Show me variation in the Goodwill column for CIMS_RAQ industry breakdown vs previous quarter | **404**: "Column 'Goodwill' was not found … Available columns: …" |

### 4–6. Absolute / %, increase / decrease, thresholds
| Query | Expected |
|---|---|
| Industries in CIMS_RAQ where total exposure changed by more than 10% vs previous quarter | \|% change\| > 10, both directions |
| Show elements with variation greater than 20% in CIMS_RAQ industry exposure vs previous quarter | \|%\| > 20 |
| Industries where total exposure decreased by more than 10% in CIMS_RAQ vs previous quarter | Decreases only, beyond -10% |
| Show changes above ₹10 crore in CIMS_RAQ industry exposure vs previous quarter | Absolute change > 100,000,000. Note: "Amounts are compared in the units the report stores" (check whether the table is in lakhs or crores) |
| Show variations between 10% and 30% in CIMS_RAQ industry exposure vs previous quarter | 10 ≤ \|%\| ≤ 30 |
| Identify changes below -15% in CIMS_RAQ industry exposure vs previous quarter | % < -15 (signed) |
| Show only increased values in CIMS_RAQ industry exposure vs previous quarter | Increases, including values new from zero |
| Show all negative variations in CIMS_RAQ industry exposure vs previous quarter | Decreases, including values that became zero |

### 7. Top / bottom
| Query | Expected |
|---|---|
| Show top 5 industries with highest variation in CIMS_RAQ total exposure vs previous quarter | **Asks absolute or %**, then returns 5 rows in rank order |
| Give me the top 10 increases in CIMS_RAQ industry exposure by percentage vs previous quarter | 10 rows, no question (measure given) |
| Which industry experienced the largest decrease in gross NPA in CIMS_RAQ vs previous quarter | 1 row |
| Give me the elements with the lowest variation in CIMS_RAQ industry exposure by percentage vs previous quarter | Up to 10 rows (plural), smallest \|%\| first |
| Rank columns based on absolute variation in CIMS_RAQ industry breakdown vs previous quarter | Columns ranked by total absolute change |

### 8–9. Unchanged / new / removed
| Query | Expected |
|---|---|
| Which industries have no variation in CIMS_RAQ total exposure vs previous quarter | Unchanged cells only |
| Find columns with zero variation in CIMS_RAQ industry breakdown vs previous quarter | Only columns where *every* value is unchanged |
| Show newly populated elements in CIMS_RAQ industry exposure vs previous quarter | Previous 0 → current non-zero. "% undefined" note |
| Which elements became zero in CIMS_RAQ industry exposure vs previous quarter | Previous non-zero → current 0 |
| Find elements where current value is zero but previous value was non-zero in CIMS_RAQ industry exposure vs previous quarter | Same as above |

### 15. Sorting
| Query | Expected |
|---|---|
| Sort by percentage variation for CIMS_RAQ industry exposure vs previous quarter | All rows, largest \|%\| first; values new from zero last (with a note) |
| Sort alphabetically by element for CIMS_RAQ industry exposure vs previous quarter | A→Z by industry name |
| Sort by current value for CIMS_RAQ industry exposure vs previous quarter | Largest current value first ("All Industries (A to S)" should top it) |
| Show highest decreases first in CIMS_RAQ industry exposure vs previous quarter | Decreases only, biggest first |

### 16. Element × column
| Query | Expected |
|---|---|
| For element D. Textiles, compare all columns in CIMS_RAQ industry breakdown vs previous quarter | 1 row, every column, no bogus "not found" note |
| Which element-column combination has the highest variation in CIMS_RAQ industry breakdown vs previous quarter | Asks the measure, then shows 1 cell |

### 12, 17, 18, 20. Anomaly / business language / data quality
| Query | Expected |
|---|---|
| Find unusual variations in CIMS_RAQ industry exposure vs previous quarter | Note starting "Rule: … outside X% to Y% (quartiles ± 1.5 × IQR) …". Fewer than 4 values gives the "Too few values" note |
| Which numbers require my attention in CIMS_RAQ industry exposure vs previous quarter | Same as the anomaly row above |
| Are there any unexpected changes in CIMS_RAQ industry exposure vs previous quarter | Same as the anomaly row above |
| Give me an executive summary of the variation in CIMS_RAQ industry exposure vs previous quarter | Summary detail block |
| Summarize increases and decreases separately in CIMS_RAQ industry exposure vs previous quarter | Summary detail with both lists |
| Give me a quick summary of important changes in CIMS_RAQ industry exposure vs previous quarter | Asks the threshold ("important" = significant), then the summary |

### 22. Calculation
| Query | Expected |
|---|---|
| How is the percentage change calculated for CIMS_RAQ industry exposure vs previous quarter | A formula note ("(current − previous) ÷ \|previous\| × 100 …") |
| What is the percentage increase in total exposure for element B.1 Sugar in CIMS_RAQ vs previous quarter | The Sugar row, increases only |

---

## 5.5: CIMS_RAQ delinquency (SEC1 Part A Domestic)

| Query | Expected |
|---|---|
| Show variance in Term Loans by period of delinquency in CIMS_RAQ vs previous quarter | Term Loans column, delinquency buckets as rows |
| Show variation for element B. NON-PERFORMING in Term Loans by period of delinquency CIMS_RAQ vs previous quarter | 1 row |
| Which delinquency buckets increased in Total Loan Assets in CIMS_RAQ vs previous quarter | Increases only |
| Show the trend of Total Loan Assets by period of delinquency in CIMS_RAQ for the last 4 quarters | Trend table with 5 dates (oldest → newest), plus rising / falling / flat / mixed counts |
| Which delinquency buckets have continuously increased over the last 4 quarters in CIMS_RAQ Term Loans | Only series that rose at every step (can be empty, and says so) |
| Identify elements with unusual movement over the last six quarters in CIMS_RAQ Term Loans | Trend plus a z-score rule note; unusual steps are underlined |

## 5.5: CIMS_RAQ investments (SEC6 Part A)
| Query | Expected |
|---|---|
| Book value held to maturity of investments in RAQ compared to last year | YoY, Book Value Held to Maturity |
| Compare Book Value Total and Market Value Total columns for investments in CIMS_RAQ vs previous quarter | 2 columns |
| Show variation only for Treasury-related elements in CIMS_RAQ investments vs previous quarter | Rows whose label contains "Treasury" ("GOI Treasury bills") |

---

## 6.0: QCB_F013 exposures by geography

| Query | Expected |
|---|---|
| Exposure to sovereigns by country in QCB_F013 vs previous quarter | Sovereigns column, countries as rows |
| Show variation for element India in QCB_F013 exposures vs previous quarter | India row only |
| Compare Sovereigns, Banks and Retail columns in QCB_F013 vs previous quarter | 3 columns |
| Which country has the largest decrease in total exposure in QCB_F013 vs previous quarter | 1 country |
| Show countries where total exposure increased by more than 25% in QCB_F013 vs previous quarter | Increases above 25% |
| Show elements where variation is more than 25% in either direction in QCB_F013 vs previous quarter | Both directions |
| Which countries became zero in QCB_F013 exposures vs previous quarter | Countries that disappeared |
| Show newly reported countries in QCB_F013 exposures vs previous quarter | Countries new from zero. Note: a country missing from the previous period entirely counts as a removed or new row |
| Compare FY 2025 with FY 2024 exposures in QCB_F013 | Fiscal year = calendar year on 6.0 |
| Compare Q1 with Q4 exposures in QCB_F013 | Q1 of the current year vs Q4 of the previous year (consecutive quarters) |
| Show total exposure in QCB_F013 for Mumbai branch vs previous quarter | Empty, with "No rows in this table have branch 'Mumbai'" (an honest refusal: there is no branch column) |

## 6.0: QCB_F014 funding by geography
| Query | Expected |
|---|---|
| Retail funding by country in QCB_F014 as of 31-Dec-2024 | 31-Dec-2024 vs the previous date |
| Compare retail funding by country in QCB_F014 with 2020 | **404**: "Data is not available for 2020 … Data is available from … to …" |
| Compare March in retail funding by country QCB_F014 | **Asks**: previous period or the same period last year |
| Show quarter-on-quarter variation of retail funding by country in QCB_F014 for the last 4 quarters | Trend, QoQ |

## 6.0: QCB_F010 maturity ladder (inflows)
| Query | Expected |
|---|---|
| Show inflows in overnight LC bucket in QCB_F010 vs previous quarter | Overnight – LC column |
| Show variation for element Cash and Cash Equivalents in QCB_F010 inflows vs previous quarter | 1 row, every bucket |
| Which maturity bucket column has the maximum increase in QCB_F010 inflows vs previous quarter | 1 column, increases only (direction respected) |
| Show top 3 inflow lines by absolute change in TOTAL - LC in QCB_F010 vs previous quarter | 3 rows |

## 6.0: QCB_F015 net open positions
| Query | Expected |
|---|---|
| Show variance for USD net open position in QCB_F015 vs previous quarter | Currency rows |
| Compare Total assets and Total liabilities columns in QCB_F015 vs previous quarter | 2 columns |
| Show currencies where Surplus/deficit decreased by more than 10% in QCB_F015 vs previous quarter | Decreases beyond 10% |

---

## 13, 23, 24. Follow-up conversation

Type these one after the other without pressing **New question**. Each answer must
keep the same return, table and dates (check the Source panel). The panel shows
"Refining the previous answer: …".

| # | Type | Expected |
|---|---|---|
| 1 | Compare industry wise total exposure in CIMS_RAQ with previous quarter | All rows |
| 2 | Show top 10 by percentage | 10 rows ranked by \|%\| |
| 3 | Only decreases | Top decreases only (up to 10) |
| 4 | More than 20% | Further cut to decreases beyond -20% |
| 5 | Show details of the first one | Drill-down table for that industry: current, previous, change and % per column, plus the stored row |
| 6 | Compare the same element for the last 6 quarters | Trend table for that one industry |
| 7 | **New question**, then: Show top 5 by percentage for industry wise exposure CIMS_RAQ vs previous quarter | Starts fresh |
| 8 | Now show absolute instead of percentage | Same 5-ranking, measure = absolute |
| 9 | Compare with previous year | Same analysis, dates now YoY |
| 10 | Show details of the ninth one | **404** "I can't tell which item you mean …" (only 5 were shown) |
| 11 | Now do the same analysis for gross NPA | Same operation, re-resolved on the new metric |

## 25. Export
| Action | Expected |
|---|---|
| After any result, click **Export CSV** | CSV opens in Excel. Header lines hold the summary, filters and source; then one line per element × column with current, previous, change, % change and status matching the screen |
| "Export only variations above 10% for CIMS_RAQ industry exposure vs previous quarter", then Export CSV | The CSV holds only the filtered rows |

## 26–27. Ask / refuse (must never guess)
| Query | Expected |
|---|---|
| Show highest variation in CIMS_RAQ industry exposure vs previous quarter | Asks absolute or % |
| Show significant changes in QCB_F013 exposures vs previous quarter | Asks the threshold; type "5 lakh" or "15%" in the box |
| Compare March in QCB_F013 exposures | Asks the period |
| Compare current data with 2020 for CIMS_RAQ industry exposure | 404, data not available, with the range that does exist |
| Show me total loans for CIMS_ROR | Refuse: the return is not indexed |
| Show me variation in the Goodwill column for QCB_F013 vs previous quarter | 404, column not found, with the list of available columns |

## Switch-off check (no regression)
Set `DV_NLP_ASK_MEASURE=false` and restart. "Show highest variation …" must then
answer straight away with the note "'Variation' was read as percentage change"
(the old behaviour). Do the same for `DV_NLP_STRICT_PERIODS=false`: "… with 2020"
then answers with the earliest date and a note.

## Known not covered
Hinglish prompts, comparing two different returns (group 21), and a branch,
product or customer dimension in these 7 tables: none of them has such a column,
so those prompts correctly answer "No rows … have …".
