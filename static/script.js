/**
 * Bin Collection App - Complete Updated Frontend JavaScript
 * Fixed to show only ONE suggestion per unique address and always display all bin types
 * Fixes syntax errors and 'value is not defined' issues
 */

class BinCollectionApp {
    constructor() {
        // API configuration - points to our Flask proxy server
        this.apiBaseURL = '/api';
        
        // Initialize DOM elements and event listeners
        this.initializeElements();
        this.attachEventListeners();
        
        // Timer for search debouncing (just initialize as null)
        this.searchTimer = null;
        
        console.log('Bin Collection App initialized');
    }

    /**
     * Get references to all DOM elements we need to manipulate
     */
    initializeElements() {
        this.addressInput = document.getElementById('addressInput');
        this.searchBtn = document.getElementById('searchBtn');
        this.suggestionsContainer = document.getElementById('suggestions');
        this.loadingDiv = document.getElementById('loading');
        this.resultsDiv = document.getElementById('results');
        this.errorDiv = document.getElementById('error');
        this.nextCollectionDiv = document.getElementById('nextCollection');
        this.addressDisplayDiv = document.getElementById('addressDisplay');
        this.errorMessageDiv = document.getElementById('errorMessage');
        
        // Create container for multiple bin results if it doesn't exist
        this.createMultipleBinContainer();
    }

    /**
     * Create a container to display results from multiple bin services
     */
    createMultipleBinContainer() {
        if (!document.getElementById('allBinsContainer')) {
            const container = document.createElement('div');
            container.id = 'allBinsContainer';
            container.className = 'all-bins-container';
            // Insert before existing results content
            this.resultsDiv.insertBefore(container, this.resultsDiv.firstChild);
        }
        this.allBinsContainer = document.getElementById('allBinsContainer');
    }

    /**
     * Attach event listeners for user interactions
     */
    attachEventListeners() {
        // Search as user types (with debouncing)
        this.addressInput.addEventListener('input', (e) => this.handleAddressInput(e));
        
        // Handle Enter key press in search box
        this.addressInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.performUnifiedSearch();
        });
        
        // Handle search button click
        this.searchBtn.addEventListener('click', () => this.performUnifiedSearch());
    }

    /**
     * Handle input changes in the address field
     * Implements debouncing to avoid excessive API calls while user is typing
     */
    handleAddressInput(event) {
        const value = event.target.value.trim();
        
        // Clear any existing search timer
        clearTimeout(this.searchTimer);
        
        // Hide previous results
        this.hideAllSections();
        
        // Don't search for very short inputs
        if (value.length < 3) {
            this.suggestionsContainer.innerHTML = '';
            return;
        }

        // Set a new timer to search after user stops typing (300ms delay)
        this.searchTimer = setTimeout(() => {
            console.log('Auto-searching for:', value);
            this.searchAddressSuggestions(value);
        }, 300);
    }

    /**
     * Search for address suggestions from all available services
     * This provides auto-complete functionality with merged results
     */
    async searchAddressSuggestions(addressText) {
        console.log('Searching for address suggestions:', addressText);
        
        try {
            this.showLoading();
            
            // Search both Maitland Council and HRR simultaneously for better performance
            const searchPromises = [
                this.fetchMaitlandAddresses(addressText),
                this.fetchHRRAddresses(addressText)
            ];
            
            // Wait for both searches to complete (or fail)
            const [maitlandResults, hrrResults] = await Promise.allSettled(searchPromises);
            
            // Extract successful results
            const maitlandAddresses = maitlandResults.status === 'fulfilled' ? maitlandResults.value : [];
            const hrrAddresses = hrrResults.status === 'fulfilled' ? hrrResults.value : [];
            
            // Display merged suggestions - FIXED to show only one per address
            this.displayMergedSuggestions(maitlandAddresses, hrrAddresses, addressText);
            
        } catch (error) {
            console.error('Error fetching address suggestions:', error);
            this.showError('Unable to search addresses. Please try again.');
        } finally {
            this.hideLoading();
        }
    }

    /**
     * Fetch address suggestions from Maitland Council API
     */
    async fetchMaitlandAddresses(addressText) {
        const url = `${this.apiBaseURL}/search-address?addressText=${encodeURIComponent(addressText)}`;
        console.log('Fetching Maitland addresses from:', url);
        
        const response = await fetch(url);
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || `Maitland API error: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('Maitland addresses received:', data.length || 0);
        return Array.isArray(data) ? data : [];
    }

    /**
     * Fetch address suggestions from HRR (Hunter Resource Recovery) API
     */
    async fetchHRRAddresses(addressText) {
        const url = `${this.apiBaseURL}/hrr-search-address?addressText=${encodeURIComponent(addressText)}`;
        console.log('Fetching HRR addresses from:', url);
        
        const response = await fetch(url);
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || `HRR API error: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('HRR addresses received:', data.length || 0);
        return Array.isArray(data) ? data : [];
    }

    /**
     * FIXED: Display only ONE suggestion per unique address
     * Eliminates duplicate entries while preserving all functionality
     */
    displayMergedSuggestions(maitlandAddresses, hrrAddresses, searchText) {
        console.log('Displaying merged suggestions');
        console.log('Maitland addresses:', maitlandAddresses);
        console.log('HRR addresses:', hrrAddresses);
        
        this.suggestionsContainer.innerHTML = '';
        
        // Create a map to merge addresses by normalized address string
        const addressMap = new Map();
        
        // Add Maitland Council addresses
        maitlandAddresses.forEach(addr => {
            const normalizedKey = this.normalizeAddressForComparison(addr.full_address);
            
            addressMap.set(normalizedKey, {
                display_address: addr.full_address,  // Use Maitland format for display (usually better)
                maitland_data: addr,
                search_address: addr.full_address,
                priority: 1  // Maitland has priority for display
            });
        });
        
        // Add HRR addresses, but DON'T create duplicates
        hrrAddresses.forEach(addr => {
            const normalizedKey = this.normalizeAddressForComparison(addr.address);
            
            if (addressMap.has(normalizedKey)) {
                // Address already exists - just add HRR data to existing entry
                const existing = addressMap.get(normalizedKey);
                existing.hrr_data = addr;
                // Keep existing display format (Maitland is usually better)
            } else {
                // New address only available in HRR
                addressMap.set(normalizedKey, {
                    display_address: addr.address,
                    hrr_data: addr,
                    search_address: addr.address,
                    priority: 2  // Lower priority
                });
            }
        });
        
        // Display suggestions - ONLY ONE per unique address
        if (addressMap.size === 0) {
            this.suggestionsContainer.innerHTML = '<div class="suggestion">No addresses found</div>';
            return;
        }

        // Create suggestion elements - ONE per unique address
        addressMap.forEach((addressData, normalizedKey) => {
            const suggestionDiv = document.createElement('div');
            suggestionDiv.className = 'suggestion';
            
            // Simple, clean display - no confusing icons
            suggestionDiv.innerHTML = `
                <div class="suggestion-address">${addressData.display_address}</div>
            `;
            
            // Click handler that gets ALL bin data using unified API
            suggestionDiv.addEventListener('click', () => {
                console.log('Selected unique address:', addressData);
                this.selectUnifiedAddress(addressData.search_address);
            });
            
            this.suggestionsContainer.appendChild(suggestionDiv);
        });
    }

    /**
     * Normalize address strings for comparison
     * Helps match similar addresses from different systems
     */
    normalizeAddressForComparison(address) {
        if (!address) return '';
        return address.toLowerCase()
                     .replace(/[^\w\s]/g, '')  // Remove punctuation
                     .replace(/\s+/g, ' ')     // Normalize whitespace
                     .replace(/street/g, 'st') // Normalize common abbreviations
                     .replace(/avenue/g, 'ave')
                     .replace(/road/g, 'rd')
                     .trim();
    }

    /**
     * NEW: Simplified address selection that always uses unified API
     * This ensures ALL three bin types are retrieved and displayed
     */
    async selectUnifiedAddress(searchAddress) {
        console.log('Selecting unified address:', searchAddress);
        
        // Update input field and clear suggestions
        this.addressInput.value = searchAddress;
        this.suggestionsContainer.innerHTML = '';
        
        try {
            this.showLoading();
            
            // Always use the unified API endpoint to get ALL bin data
            const url = `${this.apiBaseURL}/all-bins?addressText=${encodeURIComponent(searchAddress)}`;
            console.log('Unified search URL:', url);
            
            const response = await fetch(url);
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.error || `API error: ${response.status}`);
            }
            
            console.log('Unified search results:', data);
            
            // Display all bin results
            this.displayUnifiedResults(data);
            
        } catch (error) {
            console.error('Error fetching unified bin data:', error);
            this.showError(error.message || 'Unable to fetch bin collection data.');
        } finally {
            this.hideLoading();
        }
    }

    /**
     * Display results from the unified search API
     * Shows all three bin types when available
     */
    displayUnifiedResults(data) {
        console.log('Displaying unified results:', data);
        
        // Prepare results for display
        const binResults = [];
        
        // Process red bin (Maitland Council)
        if (data.bins?.red_bin && data.bins.red_bin.nextCollection) {
            binResults.push({
                type: 'red',
                service: 'Maitland Council',
                data: data.bins.red_bin,
                error: null
            });
        }
        
        // Process yellow bin (HRR)
        if (data.bins?.yellow_bin && data.bins.yellow_bin.next_collection) {
            binResults.push({
                type: 'yellow',
                service: 'Hunter Resource Recovery',
                data: data.bins.yellow_bin,
                error: null
            });
        }
        
        // Process green bin (Solo - even if it's fallback info)
        if (data.bins?.green_bin) {
            binResults.push({
                type: 'green',
                service: 'Solo Resource Recovery',
                data: data.bins.green_bin,
                error: null
            });
        }
        
        // Display results or show error if no data found
        if (binResults.length > 0) {
            this.displayAllBinData(binResults, { display_address: data.address });
        } else {
            const errors = data.errors?.join(', ') || 'No collection data found';
            this.showError(`No bin collection data available. ${errors}`);
        }
    }

    /**
     * Perform a unified search using the combined API endpoint
     * This is used when user clicks search or presses enter
     */
    async performUnifiedSearch() {
        const addressText = this.addressInput.value.trim();
        console.log('Performing unified search for:', addressText);
        
        if (addressText.length < 3) {
            this.showError('Please enter at least 3 characters');
            return;
        }

        try {
            this.showLoading();
            
            // Use the unified API endpoint that searches all services
            const url = `${this.apiBaseURL}/all-bins?addressText=${encodeURIComponent(addressText)}`;
            console.log('Unified search URL:', url);
            
            const response = await fetch(url);
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.error || `API error: ${response.status}`);
            }
            
            console.log('Unified search results:', data);
            this.displayUnifiedResults(data);
            
        } catch (error) {
            console.error('Error in unified search:', error);
            this.showError(error.message || 'Unable to search for bin collection data.');
        } finally {
            this.hideLoading();
        }
    }

    /**
     * Display bin collection data for all available services
     */
    displayAllBinData(binResults, addressInfo) {
        console.log('Displaying all bin data:', binResults, addressInfo);
        
        this.hideAllSections();
        
        // Clear previous results
        this.allBinsContainer.innerHTML = '';
        
        // Display address header
        const addressDiv = document.createElement('div');
        addressDiv.className = 'address-display';
        addressDiv.innerHTML = `<strong>Collection dates for:</strong> ${addressInfo.display_address}`;
        this.allBinsContainer.appendChild(addressDiv);
        
        // Display each bin type that has data
        binResults.forEach(result => {
            if (result.data && !result.error) {
                this.addBinCard(result.type, result.service, result.data);
            } else if (result.error) {
                console.warn(`${result.service} error:`, result.error);
            }
        });
        
        // Show additional service links
        this.addServiceLinks();
        
        this.showResults();
    }

    /**
     * Add a bin card to display collection information
     */
    addBinCard(binType, serviceName, data) {
        const config = this.getBinConfig(binType);
        
        // Extract next collection date from different possible formats
        const nextCollection = data.nextCollection || 
                              data.next_collection || 
                              (data.collection_dates && data.collection_dates[0]?.formatted_date);
        
        if (!nextCollection) {
            console.warn(`No collection date found for ${binType} bin`);
            return;
        }
        
        const card = document.createElement('div');
        card.className = `bin-card bin-${binType}`;
        card.innerHTML = `
            <div class="bin-header" style="border-left: 4px solid ${config.color};">
                <div class="bin-icon">${config.icon}</div>
                <div class="bin-info">
                    <h3>${config.title}</h3>
                    <div class="service-name" style="font-size: 0.9em; color: #666;">${serviceName}</div>
                </div>
            </div>
            <div class="collection-date" style="font-size: 1.2em; font-weight: bold; margin: 10px 0;">
                ${nextCollection}
            </div>
            ${this.addBinInstructions(binType, data)}
        `;
        
        this.allBinsContainer.appendChild(card);
    }

    /**
     * Add specific instructions for each bin type
     */
    addBinInstructions(binType, data) {
        if (binType === 'green' && data.instructions) {
            const instructions = Array.isArray(data.instructions) ? data.instructions : [];
            if (instructions.length > 0) {
                return `
                    <div class="bin-instructions" style="font-size: 0.9em; margin-top: 10px;">
                        <strong>Instructions:</strong>
                        <ul style="margin: 5px 0; padding-left: 20px;">
                            ${instructions.slice(0, 3).map(inst => `<li>${inst}</li>`).join('')}
                        </ul>
                    </div>
                `;
            }
        }
        return '';
    }

    /**
     * Get configuration for different bin types (colors, icons, titles)
     */
    getBinConfig(binType) {
        const configs = {
            red: {
                color: '#e74c3c',
                icon: '🗑️',
                title: 'General Waste Bin'
            },
            yellow: {
                color: '#f1c40f',
                icon: '♻️',
                title: 'Recycling Bin'
            },
            green: {
                color: '#27ae60',
                icon: '🌱',
                title: 'Organics Bin'
            }
        };
        
        return configs[binType] || {
            color: '#95a5a6',
            icon: '🗑️',
            title: 'Waste Bin'
        };
    }

    /**
     * Add links to external services for additional information
     */
    addServiceLinks() {
        const linksDiv = document.createElement('div');
        linksDiv.className = 'service-links';
        linksDiv.innerHTML = `
            <div class="links-header" style="margin-top: 20px; font-weight: bold;">Additional Information:</div>
            <div class="links-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-top: 10px;">
                <div class="service-link">
                    <strong>Green Organics Bin:</strong><br>
                    <a href="https://www.yourorganicsbin.com.au/" target="_blank">Visit Solo Resource Recovery</a>
                </div>
                <div class="service-link">
                    <strong>Bulky Waste Collection:</strong><br>
                    <a href="https://wasteservices.maitland.nsw.gov.au/" target="_blank">Visit Self Service Portal</a>
                </div>
                <div class="service-link">
                    <strong>Council Contact:</strong><br>
                    <a href="https://www.maitland.nsw.gov.au/council/contact-us" target="_blank">Maitland City Council</a>
                </div>
            </div>
        `;
        
        this.allBinsContainer.appendChild(linksDiv);
    }

    // UI STATE MANAGEMENT METHODS

    /**
     * Show loading state
     */
    showLoading() {
        console.log('Showing loading state');
        this.loadingDiv.classList.remove('hidden');
        this.searchBtn.disabled = true;
        this.searchBtn.textContent = 'Searching...';
    }

    /**
     * Hide loading state
     */
    hideLoading() {
        console.log('Hiding loading state');
        this.loadingDiv.classList.add('hidden');
        this.searchBtn.disabled = false;
        this.searchBtn.textContent = 'Search';
    }

    /**
     * Show results section
     */
    showResults() {
        console.log('Showing results');
        this.hideAllSections();
        this.resultsDiv.classList.remove('hidden');
    }

    /**
     * Show error message
     */
    showError(message) {
        console.log('Showing error:', message);
        this.hideAllSections();
        this.errorMessageDiv.textContent = message;
        this.errorDiv.classList.remove('hidden');
    }

    /**
     * Hide all result sections
     */
    hideAllSections() {
        this.resultsDiv.classList.add('hidden');
        this.errorDiv.classList.add('hidden');
        this.suggestionsContainer.innerHTML = '';
    }
}

// Initialize the app when the page loads
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing Bin Collection App');
    
    // Create global app instance
    window.binCollectionApp = new BinCollectionApp();
    
    // Add some global error handling
    window.addEventListener('error', (event) => {
        console.error('Global error:', event.error);
    });
    
    window.addEventListener('unhandledrejection', (event) => {
        console.error('Unhandled promise rejection:', event.reason);
    });
});
