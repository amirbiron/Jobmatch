// === JobMatch Frontend Utils ===

const API = {
    baseUrl: '/api',
    
    // Get stored JWT token
    getToken() {
        return localStorage.getItem('jm_token');
    },
    
    // Save token + user data after login/register
    saveAuth(data) {
        localStorage.setItem('jm_token', data.token);
        localStorage.setItem('jm_user', JSON.stringify(data.user));
    },
    
    // Clear auth
    logout() {
        localStorage.removeItem('jm_token');
        localStorage.removeItem('jm_user');
        window.location.href = '/login';
    },
    
    // Get current user from localStorage
    getUser() {
        const raw = localStorage.getItem('jm_user');
        return raw ? JSON.parse(raw) : null;
    },
    
    // Check if logged in
    isLoggedIn() {
        return !!this.getToken();
    },
    
    // Redirect to login if not authenticated
    requireAuth() {
        if (!this.isLoggedIn()) {
            window.location.href = '/login';
            return false;
        }
        return true;
    },
    
    // Generic API call with auth header
    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };
        
        const token = this.getToken();
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }
        
        try {
            const response = await fetch(url, {
                ...options,
                headers
            });
            
            const data = await response.json();
            
            if (response.status === 401) {
                this.logout();
                return null;
            }
            
            if (!response.ok) {
                throw new Error(data.error || 'שגיאה בשרת');
            }
            
            return data;
        } catch (err) {
            console.error(`API Error [${endpoint}]:`, err);
            throw err;
        }
    },
    
    // Shorthand methods
    get(endpoint) {
        return this.request(endpoint);
    },
    
    post(endpoint, body) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },
    
    put(endpoint, body) {
        return this.request(endpoint, {
            method: 'PUT',
            body: JSON.stringify(body)
        });
    },
    
    // File upload (no JSON content-type)
    async upload(endpoint, formData) {
        const token = this.getToken();
        const headers = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;
        
        const response = await fetch(`${this.baseUrl}${endpoint}`, {
            method: 'POST',
            headers,
            body: formData
        });
        
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'שגיאה בהעלאה');
        return data;
    }
};


// === UI Helpers ===

function showError(elementId, message) {
    const el = document.getElementById(elementId);
    if (el) {
        el.textContent = message;
        el.style.display = 'block';
    }
}

function hideError(elementId) {
    const el = document.getElementById(elementId);
    if (el) el.style.display = 'none';
}

function showLoading(buttonEl, text = 'טוען...') {
    buttonEl.disabled = true;
    buttonEl._originalText = buttonEl.textContent;
    buttonEl.textContent = text;
}

function hideLoading(buttonEl) {
    buttonEl.disabled = false;
    buttonEl.textContent = buttonEl._originalText || 'שלח';
}
