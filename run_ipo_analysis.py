import os
import time
import json
import requests
from bs4 import BeautifulSoup
import logging
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
        # Read the secret injected by GitHub Actions
        self.db_url = os.environ.get("DATABASE_URL")
        
        if not self.db_url:
            logger.warning("⚠️ No DATABASE_URL found in environment variables. Running in offline mode.")
            self.conn = None
        else:
            try:
                # Establish the live connection to Supabase PostgreSQL
                self.conn = psycopg2.connect(self.db_url)
                self.conn.autocommit = True
                logger.info("✅ Successfully connected to Supabase PostgreSQL!")
            except Exception as e:
                logger.error(f"❌ Failed to connect to Supabase: {e}")
                self.conn = None

    def upsert_ipo_record(self, ipo_symbol, data, insights, scores):
        if not self.conn:
            logger.warning("Skipping DB write because connection is not active.")
            return
            
        try:
            with self.conn.cursor() as cursor:
                # Step A: Insert company into ipo_master if not already there
                cursor.execute("""
                    INSERT INTO ipo_master (symbol, company_name, sector, issue_size_cr)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol
                    RETURNING ipo_id;
                """, (ipo_symbol, ipo_symbol.replace("_", " ").title() + " Ltd", "Technology", 500))
                ipo_id = cursor.fetchone()[0]

                # Step B: Ensure the active scoring model entry exists
                cursor.execute("""
                    INSERT INTO scoring_models (version_tag, listing_weights, long_term_weights, is_active)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (version_tag) DO UPDATE SET version_tag = EXCLUDED.version_tag
                    RETURNING model_id;
                """, ('v2.4', json.dumps({"sentiment": 0.45, "financial": 0.15}), json.dumps({"financial": 0.35})))
                model_id = cursor.fetchone()[0]

                # Step C: Save the daily assessment snapshot
                cursor.execute("""
                    INSERT INTO ipo_assessments (
                        ipo_id, model_id, financial_data, market_data, ai_insights,
                        sub_scores, listing_score, long_term_score, overall_rating
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    ipo_id,
                    model_id,
                    json.dumps(data.get('financials', {})),
                    json.dumps(data.get('gmp', {})),
                    json.dumps(insights),
                    json.dumps(scores.get('sub_scores', {})),
                    scores['listing_score'],
                    scores['long_term_score'],
                    scores['overall_rating']
                ))
                logger.info(f"💾 Successfully saved assessment snapshot for {ipo_symbol} into Supabase!")
        except Exception as e:
            logger.error(f"❌ Error writing {ipo_symbol} to Supabase: {e}")

    def log_pipeline_error(self, ipo_symbol, error_msg):
        logger.error(f"Logged pipeline error for {ipo_symbol}: {error_msg}")

# ==========================================
# 2. Real Data
class LiveDataScraper:
    def __init__(self):
        # We must use a User-Agent, otherwise financial websites will think we are a malicious bot and block us
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def discover_active_ipos(self):
        # For our test, we will target an IPO name that currently exists on the target website
        return ["Tata_Tech"] 

    def get_gmp_data(self, ipo_symbol):
        logger.info(f"🌐 Scraping live GMP data for {ipo_symbol}...")
        
        # This is a popular public URL for tracking GMP. You can change this to any site you prefer later.
        url = "https://www.investorgain.com/report/live-ipo-gmp/331/"
        
        try:
            # 1. Download the webpage
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status() 
            
            # 2. Parse the HTML with BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 3. Find the main data table on the page
            table = soup.find('table', class_='table')
            
            if not table:
                logger.warning("Could not find the GMP table in the HTML.")
                return {"current_gmp": 0, "gmp_percent": 0.0}

            # 4. Search row-by-row (<tr>) for our IPO symbol
            search_name = ipo_symbol.split("_")[0] # E.g., searching for "Tata"
            
            for row in table.find_all('tr'):
                if search_name.lower() in row.text.lower():  
                    # Found the right row! Now extract the columns (<td>)
                    columns = row.find_all('td')
                    
                    if len(columns) > 5:
                        # Extract the exact column containing the GMP (Usually column index 4 on this site)
                        # We clean the text by stripping out the '₹' symbol and commas to turn it into a real number
                        gmp_text = columns[4].text.replace('₹', '').replace(',', '').strip()
                        gmp_value = int(gmp_text) if gmp_text.isdigit() else 0
                        
                        logger.info(f"✅ Successfully scraped live GMP: ₹{gmp_value}")
                        return {"current_gmp": gmp_value, "gmp_percent": 25.0} # Keeping percent mock for now
            
            logger.warning(f"IPO {ipo_symbol} not found in the live table.")
            return {"current_gmp": 0, "gmp_percent": 0.0}

        except Exception as e:
            logger.error(f"❌ Scraping error: {e}")
            return {"current_gmp": 0, "gmp_percent": 0.0}

    # We will leave these as mock data for now while we test the GMP scraper
    def get_financials(self, ipo):
        return {"revenue_cr": 500, "pat_cr": 50, "debt_equity": 0.5, "roe": 18.5, "pe_ratio": 35.0}

    def get_subscription_data(self, ipo):
        return {"qib_x": 45.0, "nii_x": 12.0, "retail_x": 4.1}
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

                # Insert directly into Supabase
                self.db.upsert_ipo_record(ipo, validated_data, ai_insights, final_scores)
                self.alerter.check_and_trigger_alerts(ipo, final_scores, validated_data)

                logger.info(f"✅ Finished processing {ipo}")
            except Exception as e:
                logger.error(f"Pipeline failure on {ipo}: {e}")
                self.db.log_pipeline_error(ipo, str(e))

if __name__ == "__main__":
    pipeline = IPOAnalysisPipeline()
    pipeline.run_pipeline()
