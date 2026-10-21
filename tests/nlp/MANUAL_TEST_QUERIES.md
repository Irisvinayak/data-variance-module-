# NLP manual test queries

Every "first response" below was checked against the real embedding index
(`artifacts/nlp-index/ideal-55` and `ideal-60`) with the real query analyzer and
retriever. Oracle and the LLM were not involved, so the final numbers still need
checking in the app.

Legend for the first response:
- **Ask return**: a picker listing the candidate returns.
- **Ask detail**: a free-text box asking for more detail; it stays inside the return.
- **Ask date**: a picker of the table's reporting dates. "Latest vs previous" is first, and Skip means the same.
- **Result**: data shown straight away.
- **Refuse**: the named return has no embeddings.

## iDEAL 5.5 (CIMS_RAQ, CIMS_ALE Domestic / Overseas)

### Return named: only that return is searched
| Query | First response |
|---|---|
| Show variance in Term Loans by period of delinquency in CIMS_RAQ | Ask date, then CIMS_RAQ_Q_SEC1_PART_A_DOM |
| Industry wise total credit exposure in CIMS_RAQ | Ask date, then CIMS_RAQ_Q_SEC8_IND_BRKUP |
| Exposure to real estate sector in CIMS_RAQ | Ask date, then CIMS_RAQ_Q_SEC9_SENSEC_PARTB |
| Book value held to maturity of investments in RAQ | Ask date, then CIMS_RAQ_Q_SEC6_PART_A |
| Debit entries amount for accounts with banks in India in CIMS_ALE_Domestic | Ask date, then CIMS_ALE_Q_ANX_1_A |
| Balances in current accounts in CIMS_ALE Domestic | Ask date, then CIMS_ALE_Q_ANX_1_BAL |

### Date given: no date question
| Query | First response |
|---|---|
| Show variance in Term Loans in CIMS_RAQ for Q3 | Result (Q3 vs previous) |
| Gross advances in RAQ compared to last year | Result (vs same quarter last year) |
| Interest rate weighted average in CIMS_RAQ current vs previous | Result (latest vs previous) |

### Ambiguity
| Query | First response | Why |
|---|---|---|
| Show ALE data | Ask return: ALE Domestic / ALE Oversease | "ALE" fits both variants |
| Show liabilities in ALE | Ask return: ALE Domestic / ALE Oversease | same |
| Off balance sheet exposures credit equivalent in CIMS_ALE | Ask return: ALE Domestic / ALE Oversease | same |
| Credit default swap protection buyer | Ask return: ALE Domestic / ALE Oversease | the same table is in both returns |
| Movement of provisions for NPA in CIMS_RAQ | Ask detail (within RAQ) | 3 RAQ tables tie |
| Show variance in CIMS_RAQ | Ask detail (within RAQ) | no metric given |
| Show changes in Commercial Paper for RAQ last quarter | Ask detail (within RAQ) | domestic part B vs overseas part D |
| Gross NPA | Ask return | weak match, no return named |
| Gross NPA in CIMS_RAQ | Ask detail (within RAQ) | in the NPA table and in misc T4 |

### Refusal
| Query | First response |
|---|---|
| Show me total loans for CIMS_ROR | Refuse: CIMS_ROR is not indexed |

## iDEAL 6.0 (QCB F010, F013, F014, F015)

### Return named
| Query | First response |
|---|---|
| Show inflows in overnight LC bucket in QCB_F010 | Ask date, then QCB_F010_RCM_IN_QTR |
| Show outflows maturity ladder for QCB_F010 | Ask date, then QCB_F010_RCM_OUT_QTR |
| Exposure to sovereigns by country in QCB_F013 | Ask date, then QCB_F013_BREAK_EXPOSURE |
| Funding from retail by country in QCB_F014 | Ask date, then QCB_F014_FUNDING_GEO |
| Show variance for USD net open position in QCB_F015 | Ask date, then QCB_F015_FOREIGN_CUR_NOP |
| Show filing information in QCB_F013 | Ask date, then QCB_F013_FILING_INFO |
| Breakdown of funding by geography | Ask date, then QCB_F014_FUNDING_GEO |

### Date given
| Query | First response |
|---|---|
| Exposure to SMEs by country in QCB_F013 current vs previous | Result |
| Retail funding by country in QCB_F014 as of 31-Dec-2024 | Result |

### Ambiguity
| Query | First response | Why |
|---|---|---|
| Breakdown by geography | Ask return: F013 / F014 | both names say it |
| Show banks exposure by geography | Ask return: F013 / F014 | "exposure" ≠ "exposures" in the name |
| Show sovereigns by country | Ask return: F013 / F014 | the two tables have identical columns |
| Show filing information | Ask return: F010 / F013 / F014 / F015 | every return has the same filing table |
| Show reporting currency | Ask return: F010 / F013 / F014 / F015 | same |
| Compare cash inflows 1 to 7 days USD in F010 for Q3 | Ask detail (within F010) | the inflow and outflow tables tie |

### Refusal
| Query | First response |
|---|---|
| Show me total loans for QCB_F020 | Refuse: F020 is not indexed |

## Follow-up steps to check
1. Answer **Ask return** by picking a return. You should get Ask detail or Ask date for that return only.
2. On **Ask detail**, type more text (e.g. "outflows"). It must stay in the picked return.
3. On **Ask date**, try **Skip**, **Latest**, and an older date. The older date should be compared with the date before it.
