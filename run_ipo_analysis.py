import logging
import time
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger("IPO_Pipeline")

# ==========================================
# MOCK MODULES (Data Fetching, DB, AI)
# ==========================================
class APIFetcher:
    def discover_active_ipos(self):
        return ["TECH_CORP_IPO"]

    def get_financials(self, ipo):
        return {"revenue_cr": 500, "pat_cr": 50, "debt_equity": 0.5, "roe": 18.5, "pe_ratio": 35.0}

    def get_gmp_data(self, ipo):
        return {"current_gmp": 120, "gmp_percent": 28.5}

    def get_subscription_data(self, ipo):
        return {"qib_x": 45.0, "nii_x": 12.0, "retail_x": 4.1}

class DocumentExtractor:
    @staticmethod
    def extract_drhp(ipo): return "Extracted DRHP..."

class DataValidator:
    def structure_payload(self, raw_data): return raw_data # Simplified for demo

class LLMAnalyzer:
    def generate_reasoning(self, financial_context, drhp_context):
        return {
            "executive_summary": "Strong IT firm with excellent ROE, but slightly high P/E.",
            "red_flags": ["High client concentration."],
            "valuation_analysis": "Expensive",
            "sentiment_score": 0.75 # AI grades management/governance context at 75/100
        }

# ==========================================
# THE REAL SCORING ENGINE (MATH)
# ==========================================

class IPOModelEngine:
    def __init__(self, version):
        self.version = version
        
        # In production, these weights are fetched from the Database
        self.listing_weights = {
            "market_sentiment": 0.45,
            "financial_quality": 0.15,
            "valuation": 0.20,
            "business_governance": 0.20
        }
        
        self.long_term_weights = {
            "market_sentiment": 0.05,
            "financial_quality": 0.35,
            "valuation": 0.30,
            "business_governance": 0.30
        }

    def _clamp(self, value, min_val, max_val):
        """Forces a number to stay within a 0-100 boundary"""
        return max(0, min(100, (value - min_val) / (max_val - min_val) * 100))

    def _calculate_sentiment_score(self, gmp_percent, qib_sub):
        """Higher GMP and QIB subscription = better score"""
        # GMP cap at 60% premium for max score
        gmp_score = self._clamp(gmp_percent, min_val=0, max_val=60) 
        # QIB cap at 50x oversubscribed for max score
        qib_score = self._clamp(qib_sub, min_val=0, max_val=50) 
        return (gmp_score * 0.6) + (qib_score * 0.4)

    def _calculate_financial_score(self, roe, debt_equity):
        """Higher ROE is good, Higher Debt is bad"""
        roe_score = self._clamp(roe, min_val=0, max_val=30)
        # Inverted calculation for Debt: 0 D/E = 100 score, 2.0 D/E = 0 score
        debt_score = 100 - self._clamp(debt_equity, min_val=0, max_val=2.0) 
        return (roe_score * 0.7) + (debt_score * 0.3)

    def _calculate_valuation_score(self, pe_ratio):
        """Lower P/E is better (Simplified. In prod, this compares to sector average)"""
        # P/E of 10 = 100 score, P/E of 80+ = 0 score
        return 100 - self._clamp(pe_ratio, min_val=10, max_val=80)

    def calculate_scores(self, raw_data, ai_sentiment):
        # 1. Normalize sub-components to 0-100 scale
        sentiment_score = self._calculate_sentiment_score(
            raw_data['gmp']['gmp_percent'], 
            raw_data['subscription']['qib_x']
        )
        financial_score = self._calculate_financial_score(
            raw_data['financials']['roe'], 
            raw_data['financials']['debt_equity']
        )
        valuation_score = self._calculate_valuation_score(
            raw_data['financials']['pe_ratio']
        )
        governance_score = ai_sentiment * 100 # AI qualitative score mapped to 0-100
        
        # 2. Calculate Final Weighted Averages
        listing_score = (
            (sentiment_score * self.listing_weights["market_sentiment"]) +
            (financial_score * self.listing_weights["financial_quality"]) +
            (valuation_score * self.listing_weights["valuation"]) +
            (governance_score * self.listing_weights["business_governance"])
        )
        
        long_term_score = (
            (sentiment_score * self.long_term_weights["market_sentiment"]) +
            (financial_score * self.long_term_weights["financial_quality"]) +
            (valuation_score * self.long_term_weights["valuation"]) +
            (governance_score * self.long_term_weights["business_governance"])
        )
        
        # 3. Determine Final Rating Matrix
        rating = "🟡 Neutral"
        if listing_score > 75 and long_term_score > 75: rating = "🔥 Exceptional"
        elif listing_score > 65 and long_term_score > 65: rating = "🟢 Strong"
        elif listing_score < 40 or long_term_score < 40: rating = "🔴 Avoid"
        
        return {
            "listing_score": round(listing_score, 1),
            "long_term_score": round(long_term_score, 1),
            "overall_rating": rating,
            "sub_scores": {
                "sentiment": round(sentiment_score, 1),
                "financial": round(financial_score, 1),
                "valuation": round(valuation_score, 1),
                "governance": round(governance_score, 1)
            }
        }

# ==========================================
# PIPELINE EXECUTION
# ==========================================
if __name__ == "__main__":
    print("-" * 50)
    logger.info("Initializing Math & Scoring Engine...")
    
    # Fetch Data
    fetcher = APIFetcher()
    ipo = fetcher.discover_active_ipos()[0]
    
    data = {
        "financials": fetcher.get_financials(ipo),
        "gmp": fetcher.get_gmp_data(ipo),
        "subscription": fetcher.get_subscription_data(ipo)
    }
    
    # AI Engine
    ai = LLMAnalyzer()
    ai_insights = ai.generate_reasoning(data, drhp_context="...")
    
    # Math Engine
    scorer = IPOModelEngine(version="v2.4")
    results = scorer.calculate_scores(data, ai_insights['sentiment_score'])
    
    print("\n" + "="*40)
    print(f"📊 FINAL CALCULATION FOR: {ipo}")
    print("="*40)
    print(f"Sub-Scores Generated:")
    print(f"  • Market Sentiment : {results['sub_scores']['sentiment']}/100")
    print(f"  • Financial Quality: {results['sub_scores']['financial']}/100")
    print(f"  • Valuation (P/E)  : {results['sub_scores']['valuation']}/100")
    print(f"  • Gov/Business (AI): {results['sub_scores']['governance']}/100\n")
    
    print(f"🎯 Listing Gain Score   : {results['listing_score']}/100")
    print(f"🏛️ Long-Term Biz Score  : {results['long_term_score']}/100")
    print(f"⭐️ OVERALL RATING       : {results['overall_rating']}")
    print("="*40 + "\n")
