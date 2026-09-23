--------------------------------------------------------
--  Schema extract for QCB F010 / F013 / F014 / F015 tables
--  Source: D:\sql query gen\data\schema.sql
--------------------------------------------------------

--------------------------------------------------------
--  Form F014
--------------------------------------------------------

--  DDL for Table QCB_F014_FILING_INFO_DP
CREATE TABLE "QCB_F014_FILING_INFO_DP" (
    "DESCRIPTION" VARCHAR2(200),
    "VALUE"       VARCHAR2(200),
    "CODE"        VARCHAR2(200),
    "RDATE"       DATE
);

--  DDL for Table QCB_F014_FUNDING_GEO_DP
CREATE TABLE "QCB_F014_FUNDING_GEO_DP" (
    "COUNTRY"                 VARCHAR2(200),
    "SOVEREIGNS"              NUMBER(20,2),
    "MULTI_DEV_BANKS"         NUMBER(20,2),
    "PUBLIC_SECTOR_ENT"       NUMBER(20,2),
    "FOR_PROFIT_GOV_RLTD_ENT" NUMBER(20,2),
    "BANKS"                   NUMBER(20,2),
    "PVT_SEC_LARGE_CORP"      NUMBER(20,2),
    "PVT_SEC_SMES"            NUMBER(20,2),
    "NBF_INSTITUTIONS"        NUMBER(20,2),
    "RETAIL"                  NUMBER(20,2),
    "TOTAL"                   NUMBER(20,2),
    "RDATE"                   DATE
);

--------------------------------------------------------
--  Form F013
--------------------------------------------------------

--  DDL for Table QCB_F013_BREAK_EXPOSURE
CREATE TABLE "QCB_F013_BREAK_EXPOSURE" (
    "COUNTRY"                 VARCHAR2(200),
    "SOVEREIGNS"              NUMBER(20,2),
    "MULTI_DEV_BANKS"         NUMBER(20,2),
    "PUBLIC_SECTOR_ENT"       NUMBER(20,2),
    "FOR_PROFIT_GOV_RLTD_ENT" NUMBER(20,2),
    "BANKS"                   NUMBER(20,2),
    "PVT_SEC_LARGE_CORP"      NUMBER(20,2),
    "PVT_SEC_SMES"            NUMBER(20,2),
    "NBF_INSTITUTIONS"        NUMBER(20,2),
    "RETAIL"                  NUMBER(20,2),
    "TOTAL"                   NUMBER(20,2),
    "RDATE"                   DATE
);

--  DDL for Table QCB_F013_FILING_INFO
CREATE TABLE "QCB_F013_FILING_INFO" (
    "DESCRIPTION" VARCHAR2(200),
    "VALUE"       VARCHAR2(200),
    "CODE"        VARCHAR2(200),
    "RDATE"       DATE
);

--------------------------------------------------------
--  Form F010
--------------------------------------------------------

--  DDL for Table QCB_F010_FILING_INFO
CREATE TABLE "QCB_F010_FILING_INFO" (
    "DESCRIPTION" VARCHAR2(200),
    "VALUE"       VARCHAR2(200),
    "CODE"        VARCHAR2(200),
    "RDATE"       DATE
);

--  DDL for Table QCB_F010_RCM_IN_QTR
CREATE TABLE "QCB_F010_RCM_IN_QTR" (
    "SR_NO"                      NUMBER(20),
    "DESCRIPTION"                VARCHAR2(200),
    "OVERNIGHT_LC"               NUMBER(20,2),
    "OVERNIGHT_USD"              NUMBER(20,2),
    "OVERNIGHT_OTHER_FC"         NUMBER(20,2),
    "GT_1_TO_7_DAYS_LC"          NUMBER(20,2),
    "GT_1_TO_7_DAYS_USD"         NUMBER(20,2),
    "GT_1_TO_7_DAYS_OTHER_FC"    NUMBER(20,2),
    "GT_7_TO_15_DAYS_LC"         NUMBER(20,2),
    "GT_7_TO_15_DAYS_USD"        NUMBER(20,2),
    "GT_7_TO_15_DAYS_OTHER_FC"   NUMBER(20,2),
    "GT_15_TO_30_DAYS_LC"        NUMBER(20,2),
    "GT_15_TO_30_DAYS_USD"       NUMBER(20,2),
    "GT_15_TO_30_DAYS_OTHER_FC"  NUMBER(20,2),
    "GT_1_TO_3_MONTHS_LC"        NUMBER(20,2),
    "GT_1_TO_3_MONTHS_USD"       NUMBER(20,2),
    "GT_1_TO_3_MONTHS_OTHER_FC"  NUMBER(20,2),
    "GT_3_TO_6_MONTHS_LC"        NUMBER(20,2),
    "GT_3_TO_6_MONTHS_USD"       NUMBER(20,2),
    "GT_3_TO_6_MONTHS_OTHER_FC"  NUMBER(20,2),
    "GT_6_TO_12_MONTHS_LC"       NUMBER(20,2),
    "GT_6_TO_12_MONTHS_USD"      NUMBER(20,2),
    "GT_6_TO_12_MONTHS_OTHER_FC" NUMBER(20,2),
    "GT_1_TO_3_YEARS_LC"         NUMBER(20,2),
    "GT_1_TO_3_YEARS_USD"        NUMBER(20,2),
    "GT_1_TO_3_YEARS_OTHER_FC"   NUMBER(20,2),
    "GT_3_TO_5_YEARS_LC"         NUMBER(20,2),
    "GT_3_TO_5_YEARS_USD"        NUMBER(20,2),
    "GT_3_TO_5_YEARS_OTHER_FC"   NUMBER(20,2),
    "GT_5_YEARS_LC"              NUMBER(20,2),
    "GT_5_YEARS_USD"             NUMBER(20,2),
    "GT_5_YEARS_OTHER_FC"        NUMBER(20,2),
    "NO_CONTRA_MAT_LC"           NUMBER(20,2),
    "NO_CONTRA_MAT_USD"          NUMBER(20,2),
    "NO_CONTRA_MAT_OTHER_FC"     NUMBER(20,2),
    "TOTAL_LC"                   NUMBER(20,2),
    "TOTAL_USD"                  NUMBER(20,2),
    "TOTAL_OTHER_FC"             NUMBER(20,2),
    "CODE"                       VARCHAR2(20),
    "RDATE"                      DATE
);

--  DDL for Table QCB_F010_RCM_OUT_QTR
CREATE TABLE "QCB_F010_RCM_OUT_QTR" (
    "SR_NO"                      NUMBER(20),
    "DESCRIPTION"                VARCHAR2(200),
    "OVERNIGHT_LC"               NUMBER(20,2),
    "OVERNIGHT_USD"              NUMBER(20,2),
    "OVERNIGHT_OTHER_FC"         NUMBER(20,2),
    "GT_1_TO_7_DAYS_LC"          NUMBER(20,2),
    "GT_1_TO_7_DAYS_USD"         NUMBER(20,2),
    "GT_1_TO_7_DAYS_OTHER_FC"    NUMBER(20,2),
    "GT_7_TO_15_DAYS_LC"         NUMBER(20,2),
    "GT_7_TO_15_DAYS_USD"        NUMBER(20,2),
    "GT_7_TO_15_DAYS_OTHER_FC"   NUMBER(20,2),
    "GT_15_TO_30_DAYS_LC"        NUMBER(20,2),
    "GT_15_TO_30_DAYS_USD"       NUMBER(20,2),
    "GT_15_TO_30_DAYS_OTHER_FC"  NUMBER(20,2),
    "GT_1_TO_3_MONTHS_LC"        NUMBER(20,2),
    "GT_1_TO_3_MONTHS_USD"       NUMBER(20,2),
    "GT_1_TO_3_MONTHS_OTHER_FC"  NUMBER(20,2),
    "GT_3_TO_6_MONTHS_LC"        NUMBER(20,2),
    "GT_3_TO_6_MONTHS_USD"       NUMBER(20,2),
    "GT_3_TO_6_MONTHS_OTHER_FC"  NUMBER(20,2),
    "GT_6_TO_12_MONTHS_LC"       NUMBER(20,2),
    "GT_6_TO_12_MONTHS_USD"      NUMBER(20,2),
    "GT_6_TO_12_MONTHS_OTHER_FC" NUMBER(20,2),
    "GT_1_TO_3_YEARS_LC"         NUMBER(20,2),
    "GT_1_TO_3_YEARS_USD"        NUMBER(20,2),
    "GT_1_TO_3_YEARS_OTHER_FC"   NUMBER(20,2),
    "GT_3_TO_5_YEARS_LC"         NUMBER(20,2),
    "GT_3_TO_5_YEARS_USD"        NUMBER(20,2),
    "GT_3_TO_5_YEARS_OTHER_FC"   NUMBER(20,2),
    "GT_5_YEARS_LC"              NUMBER(20,2),
    "GT_5_YEARS_USD"             NUMBER(20,2),
    "GT_5_YEARS_OTHER_FC"        NUMBER(20,2),
    "NO_CONTRA_MAT_LC"           NUMBER(20,2),
    "NO_CONTRA_MAT_USD"          NUMBER(20,2),
    "NO_CONTRA_MAT_OTHER_FC"     NUMBER(20,2),
    "TOTAL_LC"                   NUMBER(20,2),
    "TOTAL_USD"                  NUMBER(20,2),
    "TOTAL_OTHER_FC"             NUMBER(20,2),
    "CODE"                       VARCHAR2(20),
    "RDATE"                      DATE
);

--------------------------------------------------------
--  Form F015
--------------------------------------------------------

--  DDL for Table QCB_F015_BANK_CAP_BASE_DP
CREATE TABLE "QCB_F015_BANK_CAP_BASE_DP" (
    "DESCRIPTION" VARCHAR2(200),
    "VALUE"       VARCHAR2(200),
    "CODE"        VARCHAR2(200),
    "RDATE"       DATE
);

--  DDL for Table QCB_F015_FILING_INFO_DP
CREATE TABLE "QCB_F015_FILING_INFO_DP" (
    "DESCRIPTION" VARCHAR2(200),
    "VALUE"       VARCHAR2(200),
    "CODE"        VARCHAR2(200),
    "RDATE"       DATE
);

--  DDL for Table QCB_F015_FOREIGN_CUR_NOP_DP
CREATE TABLE "QCB_F015_FOREIGN_CUR_NOP_DP" (
    "SR_NO"                    NUMBER(20,2),
    "CURRENCY"                 VARCHAR2(50),
    "CASH_EQ_TOTAL"            NUMBER(20,2),
    "CASH_EQ_IN_QTR"           NUMBER(20,2),
    "CASH_EQ_OUT_QTR"          NUMBER(20,2),
    "CLAIMS_CB_TOTAL"          NUMBER(20,2),
    "CLAIMS_CB_IN_QTR"         NUMBER(20,2),
    "CLAIMS_CB_OUT_QTR"        NUMBER(20,2),
    "CLAIMS_BANK_TOTAL"        NUMBER(20,2),
    "CLAIMS_BANK_IN_QTR"       NUMBER(20,2),
    "CLAIMS_BANK_OUT_QTR"      NUMBER(20,2),
    "INVEST_SEC_TOTAL"         NUMBER(20,2),
    "INVEST_SEC_IN_QTR"        NUMBER(20,2),
    "INVEST_SEC_OUT_QTR"       NUMBER(20,2),
    "LOANS_ADV_TOTAL"          NUMBER(20,2),
    "LOANS_ADV_IN_QTR"         NUMBER(20,2),
    "LOANS_ADV_OUT_QTR"        NUMBER(20,2),
    "INVEST_SUB_ASSOC_TOTAL"   NUMBER(20,2),
    "INVEST_SUB_ASSOC_IN_QTR"  NUMBER(20,2),
    "INVEST_SUB_ASSOC_OUT_QTR" NUMBER(20,2),
    "INVEST_RE_TOTAL"          NUMBER(20,2),
    "INVEST_RE_IN_QTR"         NUMBER(20,2),
    "INVEST_RE_OUT_QTR"        NUMBER(20,2),
    "NET_FIX_ASSETS_TOTAL"     NUMBER(20,2),
    "NET_FIX_ASSETS_IN_QTR"    NUMBER(20,2),
    "NET_FIX_ASSETS_OUT_QTR"   NUMBER(20,2),
    "RIGHT_USE_ASSETS_TOTAL"   NUMBER(20,2),
    "RIGHT_USE_ASSETS_IN_QTR"  NUMBER(20,2),
    "RIGHT_USE_ASSETS_OUT_QTR" NUMBER(20,2),
    "INTANG_ASSETS_TOTAL"      NUMBER(20,2),
    "INTANG_ASSETS_IN_QTR"     NUMBER(20,2),
    "INTANG_ASSETS_OUT_QTR"    NUMBER(20,2),
    "OTHER_ASSETS_TOTAL"       NUMBER(20,2),
    "OTHER_ASSETS_IN_QTR"      NUMBER(20,2),
    "OTHER_ASSETS_OUT_QTR"     NUMBER(20,2),
    "TOTAL_ASSETS"             NUMBER(20,2),
    "FORWARD_PURCHASES"        NUMBER(20,2),
    "TOTAL_ASSETS_FORWD_PURCH" NUMBER(20,2),
    "DUE_CB_TOTAL"             NUMBER(20,2),
    "DUE_CB_IN_QTR"            NUMBER(20,2),
    "DUE_CB_OUT_QTR"           NUMBER(20,2),
    "DUE_BANKS_TOTAL"          NUMBER(20,2),
    "DUE_BANKS_IN_QTR"         NUMBER(20,2),
    "DUE_BANKS_OUT_QTR"        NUMBER(20,2),
    "CUST_DEPOSITS_TOTAL"      NUMBER(20,2),
    "CUST_DEPOSITS_IN_QTR"     NUMBER(20,2),
    "CUST_DEPOSITS_OUT_QTR"    NUMBER(20,2),
    "SEC_ISSUED_TOTAL"         NUMBER(20,2),
    "SEC_ISSUED_IN_QTR"        NUMBER(20,2),
    "SEC_ISSUED_OUT_QTR"       NUMBER(20,2),
    "OTHER_BORROW_TOTAL"       NUMBER(20,2),
    "OTHER_BORROW_IN_QTR"      NUMBER(20,2),
    "OTHER_BORROW_OUT_QTR"     NUMBER(20,2),
    "LEASE_LIAB_TOTAL"         NUMBER(20,2),
    "LEASE_LIAB_IN_QTR"        NUMBER(20,2),
    "LEASE_LIAB_OUT_QTR"       NUMBER(20,2),
    "PROVISIONS_TOTAL"         NUMBER(20,2),
    "PROVISIONS_IN_QTR"        NUMBER(20,2),
    "PROVISIONS_OUT_QTR"       NUMBER(20,2),
    "OTHER_LIAB_TOTAL"         NUMBER(20,2),
    "OTHER_LIAB_IN_QTR"        NUMBER(20,2),
    "OTHER_LIAB_OUT_QTR"       NUMBER(20,2),
    "TOTAL_LIAB"               NUMBER(20,2),
    "TOTAL_EQUITY_TOTAL"       NUMBER(20,2),
    "TOTAL_EQUITY_IN_QTR"      NUMBER(20,2),
    "TOTAL_EQUITY_OUT_QTR"     NUMBER(20,2),
    "TOTAL_LIAB_EQUITY"        NUMBER(20,2),
    "DEFERRED_SALES"           NUMBER(20,2),
    "TOTAL_EQUITY_LIAB_DEFRD"  NUMBER(20,2),
    "SURPLUS_DEF"              NUMBER(20,2),
    "SURPLUS_DEF_EXCL_EQUITY"  NUMBER(20,2),
    "SURPLUS_DEF_EXCL_EQ_INV"  NUMBER(20,2),
    "SURPLUS_CAP_BASE_RATIO"   NUMBER(20,4),
    "EXCHANGE_RATE"            NUMBER(20,2),
    "RDATE"                    DATE
);
