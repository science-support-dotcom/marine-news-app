from flask import Flask, jsonify
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from dateutil import parser as date_parser
import re
import logging
import time
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.')

# In-memory cache and synchronization
cached_articles = None
last_fetch_time = 0
FETCH_INTERVAL = 60  # 60 seconds (lowered for immediate testing)
lock = threading.Lock()
# Guard to prevent starting multiple cache threads in multi-worker environments
cache_thread_started = False

# Simple CORS middleware without flask_cors
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# List of RSS sources - STRICTLY MARINE-FOCUSED
sources = [
    # International Marine News Outlets
    {"name": "The Guardian Ocean & Marine", "url": "https://www.theguardian.com/environment/oceans/rss", "type": "news"},
    {"name": "BBC Ocean & Marine", "url": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "type": "news"},
    {"name": "The Conversation Ocean", "url": "https://theconversation.com/uk/oceans/articles.atom", "type": "news"},
    
    # French Marine-Focused Sources
    {"name": "Le Monde Planète", "url": "https://www.lemonde.fr/planete/rss_full.xml", "type": "news"},
    {"name": "Le Monde Oceans", "url": "https://www.lemonde.fr/oceans/", "type": "news"},
    {"name": "Reporterre", "url": "https://reporterre.net/feed", "type": "news"},
    {"name": "Reporterre Home", "url": "https://reporterre.net/", "type": "news"},
    {"name": "Vert le Média", "url": "https://vertlemedia.com/feed", "type": "news"},
    {"name": "Vert Eco Articles", "url": "https://vert.eco/tous-les-articles", "type": "news"},
    {"name": "Le Monde Environnement", "url": "https://www.lemonde.fr/environnement/rss_full.xml", "type": "news"},
    {"name": "The Conversation Environment FR", "url": "https://theconversation.com/fr/environnement", "type": "news"},
    {"name": "Guardian Oceans", "url": "https://www.theguardian.com/environment/oceans", "type": "news"},
    {"name": "Ocean Central Stories", "url": "https://oceancentral.org/stories", "type": "news"},
    
    # Marine NGOs & International Organizations
    {"name": "Ocean Conservancy", "url": "https://oceanconservancy.org/feed/", "type": "news"},
    {"name": "WWF Marine", "url": "https://www.worldwildlife.org/feed.xml", "type": "news"},
    {"name": "Greenpeace Oceans", "url": "https://www.greenpeace.org/global/press-release/feed/", "type": "news"},
    {"name": "UNEP Oceans", "url": "https://www.unep.org/news-and-stories/feed", "type": "news"},
    {"name": "IUCN Marine", "url": "https://www.iucn.org/feeds", "type": "news"},
    
    # Marine Science & Research
    {"name": "Nature Marine Research", "url": "https://www.nature.com/research/feeds/index.rss", "type": "research"},
    {"name": "Science Daily Oceanography", "url": "https://www.sciencedaily.com/rss/earth_science/oceanography.xml", "type": "research"},
    {"name": "PubMed Marine Biology", "url": "https://www.ncbi.nlm.nih.gov/pmc/feed/", "type": "research"},
    
    # Specialized Marine Media
    {"name": "Mongabay Marine", "url": "https://feeds.mongabay.com/mongabay/latest.xml", "type": "news"},
]

# Keywords for filtering articles - STRICTLY MARINE-FOCUSED
keywords = [
    # Core Marine Terms
    "marine", "ocean", "sea", "aquatic", "pelagic", "littoral",
    "coral reef", "kelp forest", "seagrass", "mangrove", "eelgrass", "saltmarsh",
    "estuary", "coastal", "coast", "beach", "shore", "bay", "gulf", "lagoon",
    "deep sea", "abyssal", "benthic", "mesopelagic", "bathypelagic", "seabed",
    
    # Marine Biodiversity & Species
    "marine biodiversity", "fish", "whale", "dolphin", "seal", "sea lion",
    "shark", "ray", "sea turtle", "seabird", "penguin",
    "coral", "anemone", "sponge", "mollusk", "crab", "lobster", "shrimp", "oyster",
    "plankton", "krill", "zooplankton", "phytoplankton",
    
    # Fisheries & Marine Resources
    "fishery", "fishing", "fish stock", "overfishing", "sustainable fishing", "bycatch",
    "fishing pressure", "marine resource", "fisheries management",
    "aquaculture", "shellfish farming",
    
    # Pollution & Marine Health
    "plastic pollution", "ocean plastic", "microplastic", "marine debris",
    "ocean pollution", "marine pollution", "chemical pollution",
    "oil spill", "toxic contamination",
    "dead zone", "hypoxia", "acidification", "ocean acidification",
    
    # (i) Coastal and High Seas Carbon Cycling
    "carbon cycling", "coastal carbon", "high seas carbon", "blue carbon",
    "carbon sequestration", "ocean carbon", "marine carbon",
    "carbon flux", "chlorophyll", "primary production", "ocean productivity",
    "upwelling", "stratification", "thermocline",
    
    # Climate impacts on marine systems
    "ocean warming", "marine heat wave", "sea temperature",
    "sea level rise", "deoxygenation",
    
    # (ii) Nature-Based Solutions in Marine Environment
    "nature-based solution", "NBS", "ecosystem-based adaptation",
    "mangrove restoration", "seagrass restoration", "coral restoration",
    "marine protected area", "MPA", "marine reserve", "marine sanctuary",
    "no-take zone", "marine spatial planning", "blue economy",
    
    # (iii) Fisheries-Climate Ecosystem Approach
    "fisheries climate", "seafood security", "ecosystem-based fisheries",
    "community-based management", "indigenous marine", "fishing communities",
    
    # (iv) Ocean Health & Human Well-being
    "ocean health", "marine health", "nutritional security",
    "coastal livelihoods", "marine livelihoods", "vulnerable population",
    "social-ecological system", "socio-ecological resilience",
    
    # (v) Mesophotic & Deep Ocean Ecosystems
    "mesophotic", "twilight zone", "deep sea ecosystem",
    "hadal zone", "trench", "hydrothermal vent", "cold seep",
    "bioluminescence", "deep sea biodiversity",
    "deep sea mining", "mineral extraction",
    
    # (vi) Vulnerable Marine Socio-Ecological Systems & Climate Resilience
    "polar ocean", "arctic", "antarctica", "sea ice",
    "coral bleaching", "reef resilience", "reef recovery",
    "vulnerable ecosystem", "adaptation capacity", "resilience",
    
    # (vii) Migration & Habitat Changes
    "migration", "migratory species", "marine migration",
    "habitat change", "range shift", "species distribution",
    "invasive species", "connectivity", "larval dispersal"
]

keywords += [
    # French keyword translations for marine themes
    "mer", "océan", "océans", "aquatique", "pélagique", "littoral",
    "récif corallien", "forêt de varech", "herbier", "mangrove", "zostère", "marais salant",
    "estuaire", "côtier", "côte", "plage", "rivage", "baie", "golfe", "lagon",
    "mer profonde", "abyssal", "benthique", "mésopélagique", "bathypélagique", "fond marin",
    "biodiversité marine", "poisson", "baleine", "dauphin", "phoque", "otarie",
    "requin", "raie", "tortue de mer", "oiseau marin", "manchot",
    "corail", "anémone", "éponge", "mollusque", "crabe", "homard", "crevette", "huître",
    "plancton", "krill", "zooplancton", "phytoplancton",
    "pêche", "pêche durable", "surpêche", "stock de poissons", "captures accessoires",
    "pression de pêche", "ressource marine", "gestion des pêches",
    "aquaculture", "élevage de coquillages",
    "pollution plastique", "plastique océanique", "microplastique", "débris marins",
    "pollution des océans", "pollution marine", "pollution chimique",
    "marée noire", "contamination toxique",
    "zone morte", "hypoxie", "acidification", "acidification des océans",
    "cycle du carbone", "carbone côtier", "carbone en haute mer", "carbone bleu",
    "séquestration du carbone", "carbone océanique", "carbone marin",
    "flux de carbone", "chlorophylle", "production primaire", "productivité océanique",
    "upwelling", "stratification", "thermocline",
    "réchauffement des océans", "canicule marine", "température de la mer",
    "élévation du niveau de la mer", "désoxygénation",
    "solution fondée sur la nature", "solution basée sur la nature", "adaptation fondée sur les écosystèmes",
    "restauration de mangroves", "restauration des herbiers", "restauration des coraux",
    "aire marine protégée", "AMP", "réserve marine", "sanctuaire marin",
    "zone sans pêche", "planification spatiale marine", "économie bleue",
    "pêcheries climat", "sécurité alimentaire", "pêches basées sur les écosystèmes",
    "gestion communautaire", "marine autochtone", "communautés de pêcheurs",
    "santé des océans", "santé marine", "sécurité nutritionnelle",
    "moyens de subsistance côtiers", "moyens de subsistance marins", "population vulnérable",
    "système socio-écologique", "résilience socio-écologique",
    "mésophotique", "zone crépusculaire", "écosystème des grands fonds",
    "zone hadale", "fosse", "source hydrothermale", "suintement froid",
    "bioluminescence", "biodiversité des grands fonds",
    "exploitation minière des grands fonds", "extraction minière",
    "océan polaire", "arctique", "antarctique", "glace de mer",
    "blanchissement des coraux", "résilience des récifs", "récupération des récifs",
    "écosystème vulnérable", "capacité d'adaptation", "résilience",
    "migration", "espèces migratrices", "migration marine",
    "changement d'habitat", "déplacement d'aire de répartition", "distribution des espèces",
    "espèces invasives", "connectivité", "dispersion larvaire"
]

# Keywords for broader non-ocean topics that should only appear in Other News
broader_keywords = [
    "renewable", "renewables", "clean energy", "green energy", "solar", "wind", "hydro", "geothermal", "energy transition",
    "electric vehicle", "EV", "battery", "grid", "storage", "power plant", "offshore wind",
    "climate", "climate change", "global warming", "carbon", "emission", "net zero", "decarbonization",
    "environment", "environmental", "sustainability", "sustainable development", "green economy",
    "pollution", "air pollution", "water pollution", "waste", "recycling", "circular economy",
    "biodiversity", "ecosystem", "conservation", "nature", "forestry", "land use",
    "policy", "governance", "regulation", "legislation", "international agreement", "treaty", "COP"
]

broader_keywords += [
    # French translations for broader environmental topics
    "renouvelable", "énergies renouvelables", "énergie propre", "énergie verte", "solaire", "éolien", "hydroélectrique", "géothermie", "transition énergétique",
    "véhicule électrique", "VE", "batterie", "réseau", "stockage", "centrale électrique", "éolienne offshore",
    "climat", "changement climatique", "réchauffissement climatique", "carbone", "émission", "zéro net", "décarbonisation",
    "environnement", "environnemental", "durabilité", "développement durable", "économie verte",
    "pollution", "pollution de l'air", "pollution de l'eau", "déchets", "recyclage", "économie circulaire",
    "biodiversité", "écosystème", "conservation", "nature", "foresterie", "utilisation des terres",
    "politique", "gouvernance", "réglementation", "législation", "accord international", "traité", "COP"
]

def discover_feed_url(page_url):
    """Discover RSS/Atom feed URL from a page by parsing link tags."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)"}
        response = requests.get(page_url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, "html.parser")

        candidates = []
        for link in soup.find_all("link", rel=["alternate", "alternate stylesheet"]):
            href = link.get("href")
            type_attr = link.get("type", "")
            if href and ("rss" in type_attr or "atom" in type_attr or href.endswith(".xml") or href.endswith(".rss") or href.endswith(".atom")):
                candidates.append(href)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "rss" in href or "atom" in href or href.endswith(".xml"):
                candidates.append(href)

        from urllib.parse import urljoin
        for href in candidates:
            feed_url = urljoin(page_url, href)
            feed = feedparser.parse(feed_url)
            if getattr(feed, 'entries', None):
                logger.info(f"Discovered RSS/Atom feed for {page_url}: {feed_url}")
                return feed_url
    except Exception as e:
        logger.debug(f"Feed discovery failed for {page_url}: {e}")
    return None


def fetch_articles():

    """Fetch articles from RSS feeds with error handling"""
    articles = []
    seen_links = set()
    # Use timezone-aware datetime to avoid comparison issues
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=14)
    
    for source in sources:
        try:
            logger.info(f"Fetching from {source['name']}")
            feed = feedparser.parse(source["url"])
            
            if not feed.entries:
                logger.info(f"No feed entries for {source['name']}; attempting feed discovery")
                discovered = discover_feed_url(source["url"])
                if discovered:
                    feed = feedparser.parse(discovered)
                else:
                    logger.warning(f"No entries and no feed discovered for {source['name']}")
            
            if not getattr(feed, 'entries', None):
                continue
            
            for entry in feed.entries[:20]:
                try:
                    # Parse published date
                    if not hasattr(entry, 'published'):
                        published = datetime.now(timezone.utc)
                    else:
                        try:
                            published = date_parser.parse(entry.published)
                            # Convert to timezone-aware if naive
                            if published.tzinfo is None:
                                published = published.replace(tzinfo=timezone.utc)
                        except:
                            published = datetime.now(timezone.utc)
                    
                    # Check if within 14 days
                    if published < cutoff_date:
                        continue
                    
                    title = entry.get('title', 'No title')
                    link = entry.get('link', '')
                    summary = entry.get('summary', '')
                    
                    if not link:
                        continue
                    
                    # Clean up summary - remove HTML tags
                    summary = re.sub('<[^<]+?>', '', summary)[:150]
                    summary = summary.strip()
                    
                    text = (title + " " + summary).lower()
                    
                    marine_match = any(kw in text for kw in keywords)
                    broad_match = any(kw in text for kw in broader_keywords)
                    if not marine_match and not broad_match:
                        continue
                    
                    # Deduplicate
                    if link in seen_links:
                        continue
                    
                    seen_links.add(link)
                    
                    articles.append({
                        "title": title,
                        "link": link,
                        "published": published.isoformat(),
                        "source": source["name"],
                        "type": source["type"],
                        "summary": summary,
                        "other_news": broad_match and not marine_match
                    })
                except Exception as e:
                    logger.debug(f"Error processing entry: {str(e)}")
                    continue
        except Exception as e:
            logger.error(f"Error fetching from {source['name']}: {str(e)}")
            continue
    
    logger.info(f"Fetched {len(articles)} articles")
    return articles

def categorize_articles(articles):
    """Categorize articles into different types - more inclusive"""
    categories = {"Politics & Policy": [], "Research & Science": [], "General News": [], "To Watch / Read / Listen": [], "Other News": []}
    
    for art in articles:
        if art.get("other_news"):
            categories["Other News"].append(art)
            continue
        
        title_lower = art["title"].lower()
        summary_lower = art["summary"].lower()
        text = title_lower + " " + summary_lower
        source = art["source"].lower()
        
        # Check for multimedia content
        if any(word in title_lower for word in ["video", "podcast", "watch", "listen", "documentary", "interview", "webinar", "report", "white paper"]):
            categories["To Watch / Read / Listen"].append(art)
        # Check for research/academic content
        elif art["type"] == "research" or any(word in source for word in ["nature", "science daily", "pubmed", "research"]):
            categories["Research & Science"].append(art)
        # Check for policy/politics content
        elif any(word in text for word in ["policy", "politics", "legislation", "regulation", "governance", "agreement", "treaty", "negotiation", "government", "parliament", "congress", "senate", "eu", "european", "international", "cop", "climate summit", "environmental law", "sustainability goal", "net zero", "commitment"]):
            categories["Politics & Policy"].append(art)
        # Everything else goes to General News
        else:
            categories["General News"].append(art)
    
    return categories

def simple_clustering(articles):
    """Simple keyword-based clustering aligned with marine science themes"""
    if not articles:
        return {}
    
    # Define theme keywords - aligned with scientific research areas
    themes = {
        # Established themes
        "Plastic & Ocean Pollution": ["plastic", "pollution", "microplastic", "marine debris", "pollution", "contamination"],
        "Marine Biodiversity & Conservation": ["biodiversity", "species", "habitat", "extinction", "endangered", "whale", "shark", "coral"],
        "Sustainable Fisheries": ["fishery", "fishing", "fish stock", "overfishing", "bycatch", "seafood"],
        
        # (i) Coastal and High Seas Carbon Cycling
        "Blue Carbon & Carbon Cycling": ["carbon cycling", "blue carbon", "carbon sequestration", "ocean carbon", "carbon flux", "primary production"],
        
        # (ii) Nature-Based Solutions
        "Nature-Based Solutions & Restoration": ["nature-based solution", "restoration", "mangrove", "seagrass", "reef restoration", "ecosystem-based"],
        
        # (iii) Fisheries-Climate Ecosystem Approach
        "Fisheries & Climate Change Ecosystem": ["fisheries climate", "ecosystem-based fisheries", "community-based management", "food security"],
        
        # (iv) Ocean Health & Human Well-being
        "Ocean Health & Human Well-being": ["ocean health", "coastal livelihoods", "vulnerable population", "nutritional security", "socio-ecological"],
        
        # (v) Mesophotic & Deep Ocean Ecosystems
        "Deep Ocean & Mesophotic Ecosystems": ["deep sea", "mesophotic", "twilight zone", "hadal", "abyssal", "hydrothermal vent", "bioluminescence"],
        
        # (vi) Vulnerable Marine Systems & Resilience
        "Polar & Reef Ecosystems & Resilience": ["polar", "arctic", "sea ice", "coral bleaching", "reef resilience", "thermal resilience", "vulnerable ecosystem"],
        
        # (vii) Migration & Habitat Changes
        "Species Migration & Habitat Change": ["migration", "migratory", "range shift", "species distribution", "invasive species", "larval dispersal"],
        
        # Policy & Management
        "Marine Protected Areas & Policy": ["marine protected area", "MPA", "marine reserve", "marine spatial planning", "policy", "governance"],
        
        "Other Marine News": []
    }
    
    clusters = {theme: [] for theme in themes}
    
    for article in articles:
        title = article["title"].lower()
        summary = article["summary"].lower()
        text = title + " " + summary
        
        # Find best matching theme
        matched = False
        for theme, keywords_list in themes.items():
            if theme == "Other Marine News":  # Skip default
                continue
            if any(kw in text for kw in keywords_list):
                clusters[theme].append(article)
                matched = True
                break
        
        # Put unmatched articles in other
        if not matched:
            clusters["Other Marine News"].append(article)
    
    # Remove empty clusters
    return {k: v for k, v in clusters.items() if v}


def refresh_cache():
    """Background thread that refreshes the in-memory cache periodically."""
    global cached_articles, last_fetch_time
    while True:
        try:
            logger.info("Background refresh: fetching articles")
            articles = fetch_articles()
            categories = categorize_articles(articles)

            # Cluster articles within each category
            for cat in list(categories.keys()):
                if categories[cat]:
                    try:
                        categories[cat] = simple_clustering(categories[cat])
                    except Exception as e:
                        logger.debug(f"Clustering error for category {cat}: {e}")
                        categories[cat] = {}
                else:
                    categories[cat] = {}

            with lock:
                cached_articles = categories
                last_fetch_time = int(time.time())

            logger.info(f"Background refresh complete: {len(articles)} articles, updated at {last_fetch_time}")
        except Exception as e:
            logger.error(f"Error in background refresh: {e}", exc_info=True)
        # Sleep until next refresh interval
        try:
            time.sleep(FETCH_INTERVAL)
        except Exception:
            # In case sleep is interrupted for any reason, continue loop
            continue


# Start background thread at import time (safe for Gunicorn / Flask 3.x)
if not cache_thread_started:
    cache_thread_started = True
    threading.Thread(target=refresh_cache, daemon=True).start()

@app.route('/api/news')
def get_news():
    """API endpoint to return cached news. If cache is empty, report loading."""
    try:
        with lock:
            data = cached_articles

        if data is None:
            return jsonify({"status": "loading"}), 202

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error in /api/news: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    return app.send_static_file('news-aggregator.html')

if __name__ == '__main__':
    logger.info("Starting Flask server on http://localhost:5000")
    # Ensure thread is started when running directly, but avoid duplicates
    if not cache_thread_started:
        cache_thread_started = True
        threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(debug=True, host='0.0.0.0', port=5000)
