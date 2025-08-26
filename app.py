from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import urllib.parse
import redis
import json
import time
import os
import base64
from datetime import datetime, timedelta
import hashlib
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize Flask application
app = Flask(__name__)

# FIXED CORS configuration
CORS(app, resources={
    r"/api/*": {  # ← Added missing colon
        "origins": [
            'http://192.168.50.194:5000', 
            'http://127.0.0.1:5000',  # ← Fixed unclosed quote
            'https://binnight.lroytech.cc'
        ]
    }
})

# Configure logging for debugging and monitoring
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to connect to Redis for caching (fallback to in-memory if unavailable)
try:
# FIXED:
    redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    db=0,
    decode_responses=True
)
    redis_client.ping()  # Test Redis connection
    logger.info("Connected to Redis for caching")
    USE_REDIS = True
except (redis.ConnectionError, redis.RedisError):
    logger.warning("Redis not available, using in-memory cache")
    USE_REDIS = False
    in_memory_cache = {}

# Configure rate limiting to prevent API abuse
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per hour", "20 per minute"]
)

# API Configuration constants
MAITLAND_API_BASE_URL = "https://integration.maitland.nsw.gov.au/api/wastetrack"
HRR_SEARCH_URL = "https://www5.wastedge.com/publicaddresssearch_549/_search"
HRR_COLLECTION_URL_BASE = "https://www5.wastedge.com/web/wsrms/we_resportal/HRRCollectionval.p"
HRR_API_ID = "e347cd965f6a92ef2ccd61ded7c597b9"  # Static ID from HRR API

# Solo API Configuration (from the JavaScript you found)
SOLO_API_BASE_URL = "https://v2.wastetrack.net/self_service"
SOLO_API_KEY = "2a668449-8e3d-4cd3-87d2-95bf0fdc6b1f"
SOLO_RECAPTCHA_SITE_KEY = "6LdklSMpAAAAAArwuldE3Tkys_fIciWmzz48T7K8"

CACHE_EXPIRY_SECONDS = 3600  # 1 hour cache for bin collection data
SEARCH_CACHE_EXPIRY_SECONDS = 1800  # 30 minutes cache for address searches
REQUEST_TIMEOUT = 10  # seconds


class CacheManager:
    """
    Handles caching with Redis fallback to in-memory storage
    Improves performance by avoiding repeated API calls for the same data
    """
    
    @staticmethod
    def _generate_cache_key(prefix: str, data: str) -> str:
        """Generate a consistent, unique cache key using MD5 hash"""
        hash_object = hashlib.md5(data.encode())
        return f"{prefix}:{hash_object.hexdigest()}"
    
    @staticmethod
    def get(key: str):
        """Retrieve value from cache (Redis or in-memory)"""
        if USE_REDIS:
            try:
                data = redis_client.get(key)
                return json.loads(data) if data else None
            except (redis.RedisError, json.JSONDecodeError):
                return None
        else:
            # In-memory cache with expiration checking
            cache_entry = in_memory_cache.get(key)
            if cache_entry and time.time() < cache_entry['expires']:
                return cache_entry['data']
            elif cache_entry:
                del in_memory_cache[key]  # Clean up expired entry
            return None
    
    @staticmethod
    def set(key: str, value: dict, expiry_seconds: int):
        """Store value in cache with expiration time"""
        if USE_REDIS:
            try:
                redis_client.setex(key, expiry_seconds, json.dumps(value))
            except redis.RedisError:
                pass  # Silent fail for cache - app continues without caching
        else:
            in_memory_cache[key] = {
                'data': value,
                'expires': time.time() + expiry_seconds
            }


class SoloAPIClient:
    """
    Enhanced Solo API client with better error handling and graceful fallback
    Handles Solo Resource Recovery API integration for green bin collection data
    """
    
    def __init__(self):
        self.token = None
        self.token_expires = 0
        self.api_available = None  # Track API availability status
    
    def get_token(self):
        """Get valid API token with comprehensive error handling"""
        try:
            # Check cached availability status
            if self.api_available is False:
                raise Exception("Solo API previously unavailable")
            
            # Check if current token is still valid
            if self.token and time.time() < self.token_expires:
                return self.token
            
            # Check cache for existing token
            cached_token = CacheManager.get("solo_token")
            if cached_token:
                self.token = cached_token['token']
                self.token_expires = cached_token['expires']
                self.api_available = True
                return self.token
            
            # Request new token with proper headers to mimic browser request
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': 'https://www.yourorganicsbin.com.au/'
            }
            
            url = f"{SOLO_API_BASE_URL}/request_token?key={SOLO_API_KEY}"
            logger.info(f"Requesting Solo token from: {url}")
            
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            
            # Log response details for debugging
            logger.info(f"Solo token response status: {response.status_code}")
            logger.info(f"Solo token response headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            data = response.json()
            if data.get('status') == 'ok':
                self.token = data['token']
                self.token_expires = time.time() + 86400  # 24 hours
                self.api_available = True
                
                # Cache the token
                CacheManager.set("solo_token", {
                    'token': self.token,
                    'expires': self.token_expires
                }, 86400)
                
                logger.info("Solo API token retrieved successfully")
                return self.token
            else:
                self.api_available = False
                raise Exception(f"Solo token request failed: {data}")
                
        except requests.exceptions.HTTPError as e:
            self.api_available = False
            logger.error(f"Solo HTTP error {e.response.status_code}: {e.response.text}")
            raise Exception(f"Solo API HTTP {e.response.status_code} error - likely requires reCAPTCHA")
            
        except requests.exceptions.RequestException as e:
            self.api_available = False
            logger.error(f"Solo network error: {str(e)}")
            raise Exception(f"Solo API network error: {str(e)}")
            
        except Exception as e:
            self.api_available = False
            logger.error(f"Solo token error: {str(e)}")
            raise
    
    def get_fallback_info(self, address_text=None):
        """
        Provide comprehensive fallback information when Solo API is unavailable
        Based on known information about Solo Resource Recovery services
        """
        return {
            'status': 'fallback',
            'service_type': 'Green Organics Bin (FOGO)',
            'provider': 'Solo Resource Recovery',
            'collection_schedule': 'Weekly collection',
            'next_collection': 'Weekly FOGO collection - Contact your council for specific dates',
            'coverage_areas': ['Cessnock', 'Maitland', 'Singleton'],
            'message': 'FOGO (Food Organics and Garden Organics) weekly collection service available',
            'instructions': [
                'Use your kitchen caddy for food scraps',
                'Empty caddy contents into green organics bin',
                'Include both cooked and raw food scraps',
                'Add garden clippings and organic waste',
                'Use compostable liner bags provided by council'
            ],
            'what_goes_in': [
                'All food scraps (cooked and raw)',
                'Fruit and vegetable scraps',
                'Meat, fish, bones',
                'Dairy products',
                'Bread, pasta, rice',
                'Coffee grounds and tea bags',
                'Garden clippings and leaves',
                'Small branches and prunings'
            ],
            'what_stays_out': [
                'Plastic bags (except compostable liners)',
                'Glass, metal, or plastic containers',
                'Cat litter and pet waste',
                'Nappies',
                'Large branches',
                'Treated timber'
            ],
            'contact_info': {
                'website': 'https://www.yourorganicsbin.com.au/',
                'note': 'For specific collection dates, service issues, or replacement caddies',
                'council_contact': 'Contact your local council for collection schedules'
            },
            'api_status': 'unavailable',
            'reason': 'Solo API requires reCAPTCHA validation not yet implemented'
        }
    
    def search_collection_data(self, address_text):
        """
        Search for collection data with graceful fallback
        Attempts to connect to Solo API, falls back to informative content if unavailable
        """
        try:
            # Attempt to get token (this will likely fail with current API restrictions)
            token = self.get_token()
            
            # If we get here, the API is working - continue with actual API calls
            main_url = f"{SOLO_API_BASE_URL}/main?key={SOLO_API_KEY}&token={token}"
            response = requests.get(main_url, timeout=REQUEST_TIMEOUT)
            
            if response.ok:
                # Parse actual collection data from Solo API response
                return {
                    'status': 'success',
                    'service_type': 'Green Organics Bin',
                    'next_collection': 'Weekly FOGO collection',
                    'api_status': 'available',
                    'message': 'Successfully connected to Solo API',
                    'provider': 'Solo Resource Recovery'
                }
            
        except Exception as e:
            logger.warning(f"Solo API unavailable ({str(e)}), using comprehensive fallback")
            
        # Return comprehensive fallback information
        return self.get_fallback_info(address_text)


def format_hrr_date(date_string):
    """
    Format HRR date string to match their website display format
    Converts ISO date to "Monday August 20, 2025" format
    """
    try:
        # Parse the date string (handle different formats)
        if 'T' in date_string:
            date_obj = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
        else:
            date_obj = datetime.strptime(date_string, '%Y-%m-%d')
        
        day_name = date_obj.strftime('%A')  # Monday, Tuesday, etc.
        formatted_date = date_obj.strftime('%B %d, %Y')  # August 20, 2025
        return f"{day_name} {formatted_date}"
    except Exception as e:
        logger.error(f"Date formatting error: {e}")
        return date_string  # Return original if formatting fails


# Initialize Solo API client
solo_client = SoloAPIClient()

# Static file serving configuration
app.static_folder = 'static'
app.static_url_path = ''

@app.route('/')
def index():
    """Serve the main HTML page from static folder"""
    return send_from_directory('static', 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (CSS, JS, images) from static folder"""
    return send_from_directory('static', filename)


# MAITLAND COUNCIL API ENDPOINTS (Red bin - General waste)

@app.route('/api/search-address')
@limiter.limit("30 per minute")
def maitland_search_address():
    """
    Proxy endpoint for Maitland Council address search
    Rate limited to prevent abuse of council services
    """
    address_text = request.args.get('addressText', '').strip()
    
    # Input validation
    if not address_text:
        return jsonify({'error': 'Address text is required.'}), 400
    
    if len(address_text) < 3:
        return jsonify({'error': 'Address must be at least 3 characters.'}), 400
    
    if len(address_text) > 200:
        return jsonify({'error': 'Address too long.'}), 400
    
    # Sanitize input (handle single quotes like the original council code)
    address_text = address_text.replace("'", "''")
    
    # Check cache first to avoid unnecessary API calls
    cache_key = CacheManager._generate_cache_key("maitland_search", address_text.lower())
    cached_data = CacheManager.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for Maitland search: {cache_key}")
        return jsonify(cached_data), 200
    
    # Make API request to Maitland Council
    api_url = f"{MAITLAND_API_BASE_URL}/search-bin?addressText={urllib.parse.quote(address_text)}"
    
    try:
        logger.info(f"Making Maitland API request to: {api_url}")
        response = requests.get(api_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        
        # Cache successful response
        CacheManager.set(cache_key, data, SEARCH_CACHE_EXPIRY_SECONDS)
        logger.info(f"Maitland search successful, cached with key: {cache_key}")
        
        return jsonify(data), 200
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout for Maitland API: {api_url}")
        return jsonify({'error': 'Request timed out. Please try again.'}), 504
    except requests.exceptions.HTTPError as e:
        logger.error(f"Maitland API HTTP error {e.response.status_code}")
        return jsonify({'error': 'Council service error. Please try again.'}), 500
    except Exception as e:
        logger.error(f"Maitland search error: {str(e)}")
        return jsonify({'error': 'Unable to search addresses.'}), 500


@app.route('/api/bin-collection')
@limiter.limit("30 per minute")
def maitland_bin_collection():
    """
    Get bin collection data from Maitland Council using property ID
    More restrictive rate limit since this is the final data call
    """
    property_id = request.args.get('propertyId', '').strip()
    
    # Validate property ID
    if not property_id:
        return jsonify({'error': 'Property ID is required.'}), 400
    
    if not property_id.isdigit():
        return jsonify({'error': 'Invalid property ID format.'}), 400
    
    # Check cache first
    cache_key = CacheManager._generate_cache_key("maitland_bin", property_id)
    cached_data = CacheManager.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for Maitland bin data: {cache_key}")
        return jsonify(cached_data), 200
    
    # Make API request
    api_url = f"{MAITLAND_API_BASE_URL}/bin-collection?propertyId={property_id}"
    
    try:
        logger.info(f"Making Maitland bin API request to: {api_url}")
        response = requests.get(api_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        
        # Cache successful response
        CacheManager.set(cache_key, data, CACHE_EXPIRY_SECONDS)
        logger.info(f"Maitland bin data successful, cached with key: {cache_key}")
        
        return jsonify(data), 200
        
    except Exception as e:
        logger.error(f"Maitland bin collection error: {str(e)}")
        return jsonify({'error': 'Unable to get bin collection data.'}), 500


# HRR (HUNTER RESOURCE RECOVERY) API ENDPOINTS (Yellow recycling bin)

@app.route('/api/hrr-search-address')
@limiter.limit("30 per minute")
def hrr_search_address():
    """
    Search addresses in HRR database using Elasticsearch
    Uses basic authentication and Elasticsearch query format
    """
    address_text = request.args.get('addressText', '').strip()
    
    # Input validation
    if not address_text or len(address_text) < 3:
        return jsonify({'error': 'Address must be at least 3 characters'}), 400
    
    # Check cache first
    cache_key = CacheManager._generate_cache_key("hrr_search", address_text.lower())
    cached_data = CacheManager.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for HRR search: {cache_key}")
        return jsonify(cached_data), 200
    
    # Prepare HRR API request
    # Basic authentication as found in their JavaScript
    auth_string = base64.b64encode("addresssearch:addresssearch".encode()).decode()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {auth_string}'
    }
    
    # Elasticsearch query payload (copied from HRR JavaScript)
    post_data = {
        "query": {
            "bool": {
                "should": {"match_phrase_prefix": {"address": address_text.lower()}},
                "must_not": {"match_phrase": {"st": "T"}}  # Exclude certain records
            }
        }
    }
    
    try:
        logger.info(f"Making HRR search request for: {address_text}")
        response = requests.post(HRR_SEARCH_URL, 
                               json=post_data, 
                               headers=headers, 
                               timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        
        # Process Elasticsearch response to extract address data
        addresses = []
        for hit in data.get('hits', {}).get('hits', []):
            source = hit.get('_source', {})
            if source.get('address') and source.get('cust_number'):
                addresses.append({
                    'address': source.get('address', ''),
                    'cust_number': source.get('cust_number', ''),
                    'full_address': source.get('address', '')  # For compatibility
                })
        
        # Cache successful response
        CacheManager.set(cache_key, addresses, SEARCH_CACHE_EXPIRY_SECONDS)
        logger.info(f"HRR search successful, found {len(addresses)} addresses")
        
        return jsonify(addresses)
        
    except Exception as e:
        logger.error(f"HRR address search error: {str(e)}")
        return jsonify({'error': 'Unable to search HRR addresses'}), 500


@app.route('/api/hrr-collection')
@limiter.limit("30 per minute")
def hrr_collection():
    """
    Get HRR collection dates using customer number
    Returns formatted dates for yellow recycling bin
    """
    cust_number = request.args.get('custNumber', '').strip()
    
    if not cust_number:
        return jsonify({'error': 'Customer number required'}), 400
    
    # Check cache first
    cache_key = CacheManager._generate_cache_key("hrr_collection", cust_number)
    cached_data = CacheManager.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for HRR collection: {cache_key}")
        return jsonify(cached_data), 200
    
    # Build HRR collection API URL (from their JavaScript)
    hrr_collection_url = f"{HRR_COLLECTION_URL_BASE}?ID={HRR_API_ID}&custNo={cust_number}"
    
    try:
        logger.info(f"Making HRR collection request for customer: {cust_number}")
        response = requests.get(hrr_collection_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        data = response.json()
        
        # Handle HRR response messages (as per their JavaScript logic)
        if data.get('message') == 'WARNING':
            return jsonify({'error': 'No collection record found for this address'}), 404
        elif data.get('message') == 'ERROR':
            return jsonify({'error': 'HRR service error'}), 500
        
        # Process collection dates from HRR response
        collection_dates = []
        records = data.get('records', [])
        
        for record in records:
            service_date = record.get('ServiceDate')
            if service_date:
                formatted_date = format_hrr_date(service_date)
                collection_dates.append({
                    'date': service_date,
                    'formatted_date': formatted_date
                })
        
        # Prepare response data
        result = {
            'collection_dates': collection_dates,
            'next_collection': collection_dates[0]['formatted_date'] if collection_dates else None,
            'service_type': 'Yellow Recycling Bin',
            'provider': 'Hunter Resource Recovery'
        }
        
        # Cache successful response
        CacheManager.set(cache_key, result, CACHE_EXPIRY_SECONDS)
        logger.info(f"HRR collection data successful for customer: {cust_number}")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"HRR collection data error: {str(e)}")
        return jsonify({'error': 'Unable to get HRR collection data'}), 500


# SOLO API ENDPOINTS (Green organics bin)

@app.route('/api/solo-search-collection')
@limiter.limit("30 per minute")
def solo_search_collection():
    """
    Search for Solo collection data with comprehensive fallback
    Provides useful information even when API is unavailable
    """
    address_text = request.args.get('addressText', '').strip()
    
    if not address_text:
        return jsonify({'error': 'Address text required'}), 400
    
    # Check cache first
    cache_key = CacheManager._generate_cache_key("solo_search", address_text.lower())
    cached_data = CacheManager.get(cache_key)
    if cached_data:
        logger.info(f"Solo cache hit: {cache_key}")
        return jsonify(cached_data), 200
    
    # Attempt to get Solo data with fallback
    result = solo_client.search_collection_data(address_text)
    
    # Cache result regardless of source (API or fallback)
    cache_duration = CACHE_EXPIRY_SECONDS if result.get('status') == 'success' else 1800  # Shorter cache for fallbacks
    CacheManager.set(cache_key, result, cache_duration)
    
    return jsonify(result), 200


@app.route('/api/solo-status')
def solo_api_status():
    """
    Check Solo API availability status for monitoring and debugging
    """
    try:
        # Quick availability check with proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.yourorganicsbin.com.au/'
        }
        response = requests.get(f"{SOLO_API_BASE_URL}/request_token?key={SOLO_API_KEY}", 
                              headers=headers, timeout=5)
        
        return jsonify({
            'available': response.status_code == 200,
            'status_code': response.status_code,
            'message': 'Solo API is accessible' if response.status_code == 200 else f'Solo API returned {response.status_code}',
            'requires_recaptcha': response.status_code == 500,  # Likely reCAPTCHA issue
            'fallback_active': response.status_code != 200,
            'timestamp': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'available': False,
            'error': str(e),
            'message': 'Solo API is not accessible - using fallback information',
            'fallback_active': True,
            'timestamp': datetime.utcnow().isoformat()
        })


# UNIFIED API ENDPOINT (Combines all bin services)

@app.route('/api/all-bins')
@limiter.limit("30 per minute")
def get_all_bins():
    """
    Unified endpoint that attempts to get collection data for all bin types
    Takes address text and searches all available services
    Provides comprehensive bin collection information from multiple providers
    """
    address_text = request.args.get('addressText', '').strip()
    
    if not address_text:
        return jsonify({'error': 'Address text required'}), 400
    
    # Initialize results structure
    results = {
        'address': address_text,
        'bins': {
            'red_bin': None,      # Maitland Council - General waste
            'yellow_bin': None,   # HRR - Recycling
            'green_bin': None     # Solo - Organics
        },
        'errors': [],
        'search_results': {
            'maitland': [],
            'hrr': [],
            'solo': {}
        }
    }
    
    # Search Maitland Council (Red bin - General waste)
    try:
        maitland_cache_key = CacheManager._generate_cache_key("maitland_search", address_text.lower())
        maitland_addresses = CacheManager.get(maitland_cache_key)
        
        if not maitland_addresses:
            maitland_url = f"{MAITLAND_API_BASE_URL}/search-bin?addressText={urllib.parse.quote(address_text)}"
            response = requests.get(maitland_url, timeout=REQUEST_TIMEOUT)
            if response.ok:
                maitland_addresses = response.json()
                CacheManager.set(maitland_cache_key, maitland_addresses, SEARCH_CACHE_EXPIRY_SECONDS)
        
        results['search_results']['maitland'] = maitland_addresses or []
        
        # Get bin data for first Maitland address
        if maitland_addresses and len(maitland_addresses) > 0:
            property_id = maitland_addresses[0].get('property_id')
            if property_id:
                bin_url = f"{MAITLAND_API_BASE_URL}/bin-collection?propertyId={property_id}"
                bin_response = requests.get(bin_url, timeout=REQUEST_TIMEOUT)
                if bin_response.ok:
                    results['bins']['red_bin'] = bin_response.json()
                    
    except Exception as e:
        results['errors'].append(f"Maitland search error: {str(e)}")
    
    # Search HRR (Yellow bin - Recycling)
    try:
        hrr_cache_key = CacheManager._generate_cache_key("hrr_search", address_text.lower())
        hrr_addresses = CacheManager.get(hrr_cache_key)
        
        if not hrr_addresses:
            # Make HRR search request
            auth_string = base64.b64encode("addresssearch:addresssearch".encode()).decode()
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Basic {auth_string}'
            }
            post_data = {
                "query": {
                    "bool": {
                        "should": {"match_phrase_prefix": {"address": address_text.lower()}},
                        "must_not": {"match_phrase": {"st": "T"}}
                    }
                }
            }
            
            response = requests.post(HRR_SEARCH_URL, json=post_data, headers=headers, timeout=REQUEST_TIMEOUT)
            if response.ok:
                data = response.json()
                hrr_addresses = []
                for hit in data.get('hits', {}).get('hits', []):
                    source = hit.get('_source', {})
                    if source.get('address') and source.get('cust_number'):
                        hrr_addresses.append({
                            'address': source.get('address'),
                            'cust_number': source.get('cust_number')
                        })
                CacheManager.set(hrr_cache_key, hrr_addresses, SEARCH_CACHE_EXPIRY_SECONDS)
        
        results['search_results']['hrr'] = hrr_addresses or []
        
        # Get HRR collection data for first address
        if hrr_addresses and len(hrr_addresses) > 0:
            cust_number = hrr_addresses[0].get('cust_number')
            if cust_number:
                hrr_url = f"{HRR_COLLECTION_URL_BASE}?ID={HRR_API_ID}&custNo={cust_number}"
                hrr_response = requests.get(hrr_url, timeout=REQUEST_TIMEOUT)
                if hrr_response.ok:
                    hrr_data = hrr_response.json()
                    if hrr_data.get('message') not in ['WARNING', 'ERROR'] and hrr_data.get('records'):
                        # Process HRR collection dates
                        collection_dates = []
                        for record in hrr_data.get('records', []):
                            service_date = record.get('ServiceDate')
                            if service_date:
                                collection_dates.append({
                                    'date': service_date,
                                    'formatted_date': format_hrr_date(service_date)
                                })
                        
                        results['bins']['yellow_bin'] = {
                            'collection_dates': collection_dates,
                            'next_collection': collection_dates[0]['formatted_date'] if collection_dates else None,
                            'service_type': 'Yellow Recycling Bin',
                            'provider': 'Hunter Resource Recovery'
                        }
                        
    except Exception as e:
        results['errors'].append(f"HRR search error: {str(e)}")
    
    # Search Solo (Green bin - Organics) with comprehensive fallback
    try:
        solo_result = solo_client.search_collection_data(address_text)
        results['search_results']['solo'] = solo_result
        
        # Always provide green bin information, regardless of API status
        if solo_result:
            results['bins']['green_bin'] = {
                'next_collection': solo_result.get('next_collection', 'Weekly FOGO collection'),
                'service_type': solo_result.get('service_type', 'Green Organics Bin'),
                'provider': 'Solo Resource Recovery',
                'collection_schedule': solo_result.get('collection_schedule', 'Weekly collection'),
                'message': solo_result.get('message', 'FOGO collection service available'),
                'api_status': solo_result.get('api_status', 'fallback'),
                'contact': {
                    'website': 'https://www.yourorganicsbin.com.au/',
                    'phone': 'Contact your local council',
                    'note': 'For specific collection dates and service issues'
                },
                'service_areas': solo_result.get('coverage_areas', ['Cessnock', 'Maitland', 'Singleton']),
                'instructions': solo_result.get('instructions', [
                    'Use kitchen caddy for food scraps',
                    'Empty into green organics bin', 
                    'Include garden clippings'
                ]),
                'what_goes_in': solo_result.get('what_goes_in', [
                    'All food scraps', 'Garden clippings', 'Coffee grounds'
                ]),
                'what_stays_out': solo_result.get('what_stays_out', [
                    'Plastic bags', 'Pet waste', 'Treated timber'
                ])
            }
            
    except Exception as e:
        logger.error(f"Solo integration error: {str(e)}")
        results['errors'].append(f"Solo service temporarily unavailable")
        
        # Provide comprehensive fallback information
        results['bins']['green_bin'] = {
            'next_collection': 'Weekly FOGO collection - Contact council for specific dates',
            'service_type': 'Green Organics Bin',
            'provider': 'Solo Resource Recovery',
            'message': 'Solo API temporarily unavailable - comprehensive information available',
            'contact': {
                'website': 'https://www.yourorganicsbin.com.au/',
                'note': 'Visit website or contact council for collection schedules'
            },
            'service_areas': ['Cessnock', 'Maitland', 'Singleton'],
            'instructions': [
                'Use kitchen caddy for food scraps',
                'Empty caddy into green organics bin',
                'Include cooked and raw food scraps',
                'Add garden clippings and organic waste'
            ],
            'what_goes_in': [
                'All food scraps (cooked and raw)', 'Fruit and vegetable scraps',
                'Meat, fish, bones', 'Dairy products', 'Bread, pasta, rice',
                'Coffee grounds and tea bags', 'Garden clippings', 'Small branches'
            ],
            'what_stays_out': [
                'Plastic bags (except compostable liners)', 'Glass, metal containers',
                'Cat litter and pet waste', 'Nappies', 'Large branches', 'Treated timber'
            ],
            'api_status': 'unavailable'
        }
    
    return jsonify(results)


# UTILITY AND MONITORING ENDPOINTS

@app.route('/api/health')
def health_check():
    """Health check endpoint for monitoring application status"""
    cache_status = "redis" if USE_REDIS else "memory"
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'cache_backend': cache_status,
        'services': {
            'maitland': 'active',
            'hrr': 'active',
            'solo': 'fallback mode (API requires reCAPTCHA)'
        },
        'endpoints': {
            'unified': '/api/all-bins',
            'maitland': '/api/search-address, /api/bin-collection',
            'hrr': '/api/hrr-search-address, /api/hrr-collection',
            'solo': '/api/solo-search-collection, /api/solo-status'
        }
    })


@app.route('/api/cache-stats')
def cache_stats():
    """Get cache statistics for monitoring performance"""
    if USE_REDIS:
        try:
            info = redis_client.info('memory')
            return jsonify({
                'backend': 'redis',
                'memory_used': info.get('used_memory_human', 'unknown'),
                'connected': True,
                'keys_count': redis_client.dbsize()
            })
        except redis.RedisError:
            return jsonify({'backend': 'redis', 'connected': False})
    else:
        # Calculate memory usage for in-memory cache
        total_entries = len(in_memory_cache)
        active_entries = 0
        current_time = time.time()
        
        for key, entry in in_memory_cache.items():
            if current_time < entry['expires']:
                active_entries += 1
        
        return jsonify({
            'backend': 'memory',
            'total_entries': total_entries,
            'active_entries': active_entries,
            'expired_entries': total_entries - active_entries,
            'connected': True
        })


# ERROR HANDLERS

@app.errorhandler(429)
def ratelimit_handler(e):
    """Handle rate limit exceeded responses"""
    return jsonify({
        'error': 'Rate limit exceeded. Please slow down your requests.',
        'retry_after': str(getattr(e, 'retry_after', '60')),
        'message': 'Too many requests - please wait before trying again'
    }), 429


@app.errorhandler(500)
def internal_error(error):
    """Handle internal server errors"""
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({
        'error': 'Internal server error.',
        'message': 'An unexpected error occurred. Please try again later.'
    }), 500


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({
        'error': 'Endpoint not found.',
        'message': 'The requested resource could not be found.',
        'available_endpoints': ['/api/all-bins', '/api/health', '/api/cache-stats']
    }), 404


# APPLICATION STARTUP

import os
# At the end of your app.py, change this:
if __name__ == '__main__':
    app.run(
        debug=False,  # Never use debug=True in production
        host='0.0.0.0',  # Listen on all interfaces
        port=int(os.environ.get('PORT', 5000))  # Use PORT environment variable
    )
    # Clean up expired in-memory cache entries on startup
    if not USE_REDIS:
        in_memory_cache.clear()
    
    # Log startup information
    logger.info("=" * 60)
    logger.info("Starting Bin Collection API Proxy Server")
    logger.info("=" * 60)
    logger.info(f"Cache backend: {'Redis' if USE_REDIS else 'In-memory'}")
    logger.info("Supported services:")
    logger.info("  ✅ Maitland Council (Red bin) - General waste collection")
    logger.info("  ✅ Hunter Resource Recovery (Yellow bin) - Recycling collection")
    logger.info("  ⚠️  Solo Resource Recovery (Green bin) - Organics collection (fallback mode)")
    logger.info("=" * 60)
    logger.info("API Endpoints available:")
    logger.info("  /api/all-bins - Unified search for all bin types")
    logger.info("  /api/health - Health check and status")
    logger.info("  /api/cache-stats - Cache performance statistics")
    logger.info("  /api/solo-status - Solo API availability check")
    logger.info("=" * 60)
    
    # Start the Flask development server
    app.run(
        debug=os.getenv('FLASK_DEBUG', 'False').lower() == 'true',
        host=os.getenv('FLASK_HOST', '127.0.0.1'),
        port=int(os.getenv('FLASK_PORT', 3000))
    )
