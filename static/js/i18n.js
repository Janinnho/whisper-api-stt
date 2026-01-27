/**
 * Internationalization (i18n) module for Whisper API STT
 * Supports English and German with browser language detection
 */
class I18n {
    constructor() {
        this.currentLang = 'en';
        this.translations = {};
        this.fallback = {};
        this.initialized = false;
    }

    /**
     * Initialize the i18n system
     */
    async init() {
        this.currentLang = this.detectLanguage();

        // Load translations
        try {
            const response = await fetch(`/static/i18n/${this.currentLang}.json`);
            if (response.ok) {
                this.translations = await response.json();
            }
        } catch (e) {
            console.warn(`Failed to load ${this.currentLang} translations`);
        }

        // Load fallback (English) if not already loaded
        if (this.currentLang !== 'en') {
            try {
                const response = await fetch('/static/i18n/en.json');
                if (response.ok) {
                    this.fallback = await response.json();
                }
            } catch (e) {
                console.warn('Failed to load English fallback translations');
            }
        }

        this.initialized = true;
        this.translatePage();
    }

    /**
     * Detect the preferred language
     */
    detectLanguage() {
        // 1. Check user preference in localStorage
        const stored = localStorage.getItem('language');
        if (stored && this.isSupported(stored)) {
            return stored;
        }

        // 2. Check browser language
        const browserLang = navigator.language.split('-')[0].toLowerCase();
        if (this.isSupported(browserLang)) {
            return browserLang;
        }

        // 3. Default to English
        return 'en';
    }

    /**
     * Check if a language is supported
     */
    isSupported(lang) {
        return ['en', 'de'].includes(lang);
    }

    /**
     * Set the current language
     */
    async setLanguage(lang) {
        if (!this.isSupported(lang)) {
            console.warn(`Language ${lang} is not supported`);
            return;
        }

        this.currentLang = lang;
        localStorage.setItem('language', lang);

        // Reload translations
        try {
            const response = await fetch(`/static/i18n/${lang}.json`);
            if (response.ok) {
                this.translations = await response.json();
            }
        } catch (e) {
            console.warn(`Failed to load ${lang} translations`);
        }

        this.translatePage();

        // Sync to server if logged in
        this.syncToServer();
    }

    /**
     * Get current language
     */
    getLanguage() {
        return this.currentLang;
    }

    /**
     * Translate a key to the current language
     * @param {string} key - Dot-notation key (e.g., "nav.transcribe")
     * @param {object} params - Parameters for interpolation
     */
    t(key, params = {}) {
        const keys = key.split('.');
        let value = this.translations;

        // Try to find in current language
        for (const k of keys) {
            value = value?.[k];
        }

        // Fallback to English
        if (value === undefined && this.fallback) {
            value = this.fallback;
            for (const k of keys) {
                value = value?.[k];
            }
        }

        // Return key if not found
        if (typeof value !== 'string') {
            return key;
        }

        // Replace parameters: {name} -> params.name
        return value.replace(/\{(\w+)\}/g, (_, p) => {
            return params[p] !== undefined ? params[p] : `{${p}}`;
        });
    }

    /**
     * Translate all elements with data-i18n attribute
     */
    translatePage() {
        // Translate text content
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            el.textContent = this.t(key);
        });

        // Translate placeholders
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            el.placeholder = this.t(key);
        });

        // Translate titles
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.getAttribute('data-i18n-title');
            el.title = this.t(key);
        });

        // Translate aria-labels
        document.querySelectorAll('[data-i18n-aria]').forEach(el => {
            const key = el.getAttribute('data-i18n-aria');
            el.setAttribute('aria-label', this.t(key));
        });

        // Update HTML lang attribute
        document.documentElement.lang = this.currentLang;
    }

    /**
     * Sync language preference to server
     */
    async syncToServer() {
        try {
            await fetch('/api/me/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ language: this.currentLang })
            });
        } catch (e) {
            // Ignore errors for anonymous users
        }
    }
}

// Create global instance
window.i18n = new I18n();

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.i18n.init();
});
