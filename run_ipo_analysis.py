import google.generativeai as genai
import os
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
import psycopg2

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("IPO_Pipeline")

# ==========================================
# 1. REAL SUPABASE DATABASE CLIENT
# ==========================================

class PostgresClient:
    def __init__(self):
        self.db_url = os.environ.get("DATABASE_URL")
        if not self.db_url:
            logger.warning("⚠️ No DATABASE_URL found in environment variables.")
            self.conn = None
        else:
            try:
                self.conn = psycopg2.connect(self.db_url)
                self.conn.autocommit = True
                logger.info("✅ Successfully connected to Supabase PostgreSQL!")
            except Exception as e:
                logger.error(f"❌ Failed to connect to Supabase: {e}")
                self.conn = None

    def upsert_ipo_record(self, ipo_symbol, data, insights, scores):
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO ipo_master (symbol, company_name, sector, issue_size_cr)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol
                    RETURNING ipo_id;
                """, (ipo_symbol, ipo_symbol.replace("_", " ").title() + " Ltd", "Technology", 500))
                ipo_id = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO scoring_models (version_tag, listing_weights, long_term_weights, is_active)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (version_tag) DO UPDATE SET version_tag = EXCLUDED.version_tag
                    RETURNING model_id;
                """, ('v2.4', json.dumps({"sentiment": 0.45, "financial": 0.15}), json.dumps({"financial": 0.35})))
                model_id = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO ipo_assessments (
                        ipo_id, model_id, financial_data, market_data, ai_insights,
                        sub_scores, listing_score, long_term_score, overall_rating
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    ipo_id, model_id, json.dumps(data.get('financials', {})),
                    json.dumps(data.get('gmp', {})), json.dumps(insights),
                    json.dumps(scores.get('sub_scores', {})),
                    scores['listing_score'], scores['long_term_score'], scores['overall_rating']
                ))
                logger.info(f"💾 Successfully saved snapshot for {ipo_symbol}!")
        except Exception as e:
            logger.error(f"❌ Error writing {ipo_symbol} to Supabase: {e}")

    def log_pipeline_error(self, ipo_symbol, error_msg):
        logger.error(f"Logged pipeline error for {ipo_symbol}: {error_msg}")

# ==========================================
# 2. REAL LIVE DATA SCRAPER
# ==========================================

class LiveDataScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def discover_active_ipos(self):
        logger.info("🔍 Scanning website for the latest IPOs...")
        url = "https://www.investorgain.com/report/live-ipo-gmp/331/"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status() 
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table', class_='table')
            
            if not table:
                logger.warning("Could not find the IPO table. Falling back to default.")
                return ["Tata_Tech"]

            active_ipos = []
            
            # Skip the header row, loop through the top data rows
            for row in table.find_all('tr')[1:]:
                columns = row.find_all('td')
                if len(columns) > 0:
                    # Extract the IPO name from the first column (e.g., "Bajaj Housing Finance IPO")
                    raw_name = columns[0].text.strip()
                    
                    # Clean it up to use as our database ID: remove " IPO" and replace spaces with underscores
                    clean_name = raw_name.replace(" IPO", "").replace(" SME", "").replace(" ", "_")
                    
                    if clean_name:
                        active_ipos.append(clean_name)
                
                # SAFETY LIMIT: Only grab the top 3 most recent IPOs to prevent AI rate limit bans!
                if len(active_ipos) >= 3:
                    break
                    
            logger.info(f"🎯 Auto-discovered top IPOs: {active_ipos}")
            return active_ipos

        except Exception as e:
            logger.error(f"❌ Failed to discover IPOs: {e}")
            return ["Tata_Tech"] # Fallback so the script doesn't crash

    def get_gmp_data(self, ipo_symbol):
        logger.info(f"🌐 Scraping live GMP data for {ipo_symbol}...")
        url = "https://www.investorgain.com/report/live-ipo-gmp/331/"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status() 
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table', class_='table')
            
            if not table:
                return {"current_gmp": 0, "gmp_percent": 0.0}

            search_name = ipo_symbol.split("_")[0] 
            for row in table.find_all('tr'):
                if search_name.lower() in row.text.lower():  
                    columns = row.find_all('td')
                    if len(columns) > 5:
                        gmp_text = columns[4].text.replace('₹', '').replace(',', '').strip()
                        gmp_value = int(gmp_text) if gmp_text.isdigit() else 0
                        logger.info(f"✅ Successfully scraped live GMP: ₹{gmp_value}")
                        return {"current_gmp": gmp_value, "gmp_percent": 25.0}
            
            logger.warning(f"IPO {ipo_symbol} not found in the live table.")
            return {"current_gmp": 0, "gmp_percent": 0.0}
        except Exception as e:
            logger.error(f"❌ Scraping error: {e}")
            return {"current_gmp": 0, "gmp_percent": 0.0}

    def get_financials(self, ipo):
        return {"revenue_cr": 500, "pat_cr": 50, "debt_equity": 0.5, "roe": 18.5, "pe_ratio": 35.0}

    def get_subscription_data(self, ipo):
        return {"qib_x": 45.0, "nii_x": 12.0, "retail_x": 4.1}

# ==========================================
# 2.5 RESTORED MOCK AI & HELPER CLASSES
# ==========================================

class DocumentExtractor:
    @staticmethod
    def extract_drhp(ipo):
        return "DRHP document text placeholder..."

class DataValidator:
    def structure_payload(self, raw_data):
        return raw_data

class LLMAnalyzer:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("⚠️ No GEMINI_API_KEY found! AI will fail.")
        else:
            genai.configure(api_key=api_key)
        
        # Hardcoding the exact model version Google's API instructed us to use
        self.model_name = 'gemini-3.6-flash'
        self.model = genai.GenerativeModel(self.model_name)

    def generate_reasoning(self, financial_context, drhp_context):
        logger.info(f"🧠 Asking {self.model_name} to analyze the IPO data...")
        
        prompt = f"""
        You are an expert financial analyst evaluating an IPO. 
        Based on the following financial and market data: {json.dumps(financial_context)}
        
        Provide a strict JSON response with exactly these keys:
        - "executive_summary" (string)
        - "bull_case" (string)
        - "bear_case" (string)
        - "key_positives" (list of strings)
        - "key_negatives" (list of strings)
        - "red_flags" (list of strings)
        - "valuation_analysis" (string)
        - "sentiment_score" (float between 0.0 and 1.0)
        
        Output ONLY valid JSON. Do not include markdown formatting or extra text.
        """
        
        try:
            response = self.model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(response_mime_type="application/json")
            )
            
            ai_insights = json.loads(response.text)
            logger.info("✅ Gemini AI successfully generated insights!")
            return ai_insights
            
        except Exception as e:
            logger.error(f"❌ AI Generation Failed: {e}")
            return {
                "executive_summary": "AI generation failed.",
                "bull_case": "N/A", "bear_case": "N/A",
                "key_positives": [], "key_negatives": [], "red_flags": [],
                "valuation_analysis": "N/A", "sentiment_score": 0.5
            }
            
class IPOModelEngine:
    def __init__(self, version):
        self.version = version

    def calculate_scores(self, data, ai_sentiment):
        return {
            "listing_score": 82.5, "long_term_score": 74.0, "overall_rating": "🟢 Strong",
            "sub_scores": {"sentiment": 85.0, "financial": 72.0, "valuation": 60.0, "governance": 75.0}
        }

class NotificationService:
    def check_and_trigger_alerts(self, ipo, scores, data):
        if scores["listing_score"] > 80:
            logger.info(f"🔔 ALERT: {ipo} crossed listing score threshold of 80!")

# ==========================================
# 3. PIPELINE ORCHESTRATOR
# ==========================================

class IPOAnalysisPipeline:
    def __init__(self):
        self.db = PostgresClient()
        self.fetcher = LiveDataScraper()
        self.validator = DataValidator()
        self.ai = LLMAnalyzer()
        self.scoring = IPOModelEngine(version="v2.4")
        self.alerter = NotificationService()

    def run_pipeline(self):
        logger.info("Starting IPO Pipeline execution...")
        target_ipos = self.fetcher.discover_active_ipos()

        for ipo in target_ipos:
            try:
                logger.info(f"--- Processing: {ipo} ---")
                data = {
                    "financials": self.fetcher.get_financials(ipo),
                    "gmp": self.fetcher.get_gmp_data(ipo),
                    "subscription": self.fetcher.get_subscription_data(ipo)
                }
                drhp_text = DocumentExtractor.extract_drhp(ipo)
                validated_data = self.validator.structure_payload(data)

                ai_insights = self.ai.generate_reasoning(validated_data, drhp_text)
                final_scores = self.scoring.calculate_scores(validated_data, ai_insights["sentiment_score"])

                self.db.upsert_ipo_record(ipo, validated_data, ai_insights, final_scores)
                self.alerter.check_and_trigger_alerts(ipo, final_scores, validated_data)

                logger.info(f"✅ Finished processing {ipo}")
            except Exception as e:
                logger.error(f"Pipeline failure on {ipo}: {e}")
                self.db.log_pipeline_error(ipo, str(e))

if __name__ == "__main__":
    pipeline = IPOAnalysisPipeline()
    pipeline.run_pipeline()
