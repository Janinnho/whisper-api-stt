/**
 * Main application JavaScript for Whisper API STT
 * Handles theme management and common functionality
 */

/**
 * Theme Manager - handles light/dark/auto themes
 */
class ThemeManager {
    constructor() {
        this.theme = this.getStoredTheme();
        this.mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

        // Apply initial theme
        this.apply();

        // Listen for system theme changes
        this.mediaQuery.addEventListener('change', () => {
            if (this.theme === 'auto') {
                this.apply();
            }
        });
    }

    /**
     * Get stored theme preference
     */
    getStoredTheme() {
        const stored = localStorage.getItem('theme');
        if (stored && ['light', 'dark', 'auto'].includes(stored)) {
            return stored;
        }
        return 'auto';
    }

    /**
     * Get the effective theme (resolves 'auto' to actual theme)
     */
    getEffectiveTheme() {
        if (this.theme === 'auto') {
            return this.mediaQuery.matches ? 'dark' : 'light';
        }
        return this.theme;
    }

    /**
     * Apply the current theme to the document
     */
    apply() {
        const effective = this.getEffectiveTheme();
        document.documentElement.setAttribute('data-theme', effective);

        // Update toggle buttons if they exist
        this.updateToggleButtons();
    }

    /**
     * Set the theme
     */
    setTheme(theme) {
        if (!['light', 'dark', 'auto'].includes(theme)) {
            return;
        }

        this.theme = theme;
        localStorage.setItem('theme', theme);
        this.apply();

        // Sync to server if logged in
        this.syncToServer();
    }

    /**
     * Cycle through themes
     */
    cycle() {
        const themes = ['light', 'dark', 'auto'];
        const currentIndex = themes.indexOf(this.theme);
        const nextTheme = themes[(currentIndex + 1) % themes.length];
        this.setTheme(nextTheme);
    }

    /**
     * Update toggle button states
     */
    updateToggleButtons() {
        const buttons = document.querySelectorAll('.theme-toggle button');
        buttons.forEach(btn => {
            const btnTheme = btn.getAttribute('data-theme');
            if (btnTheme === this.theme) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    }

    /**
     * Sync theme to server
     */
    async syncToServer() {
        try {
            await fetch('/api/me/preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: this.theme })
            });
        } catch (e) {
            // Ignore errors for anonymous users
        }
    }
}

// Create global theme manager instance
window.themeManager = new ThemeManager();

/**
 * Tab management
 */
function openTab(evt, tabId) {
    // Hide all tab contents
    const tabContents = document.getElementsByClassName('tab-content');
    for (let i = 0; i < tabContents.length; i++) {
        tabContents[i].classList.remove('active');
    }

    // Remove active from all tabs
    const tabs = document.getElementsByClassName('tab');
    for (let i = 0; i < tabs.length; i++) {
        tabs[i].classList.remove('active');
    }

    // Show selected tab content
    const selectedTab = document.getElementById(tabId);
    if (selectedTab) {
        selectedTab.classList.add('active');
    }

    // Mark tab button as active
    if (evt && evt.currentTarget) {
        evt.currentTarget.classList.add('active');
    }

    // Trigger tab-specific load functions
    if (tabId === 'jobs-tab' && typeof loadJobs === 'function') {
        loadJobs();
    }
    if (tabId === 'history-tab' && typeof loadHistory === 'function') {
        loadHistory();
    }
    if (tabId === 'users-tab' && typeof loadUsers === 'function') {
        loadUsers();
    }
    if (tabId === 'admin-tab' && typeof loadAdminData === 'function') {
        loadAdminData();
    }
}

/**
 * Copy text to clipboard
 */
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (e) {
        console.error('Failed to copy to clipboard:', e);
        return false;
    }
}

/**
 * Download content as file
 */
function downloadFile(content, filename, mimeType = 'text/plain') {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 500);
}

/**
 * Format seconds as HH:MM:SS
 */
function formatTime(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    const h = String(Math.floor(seconds / 3600)).padStart(2, '0');
    const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    const s = String(seconds % 60).padStart(2, '0');
    return `${h}:${m}:${s}`;
}

/**
 * Format date for display
 */
function formatDate(isoString) {
    if (!isoString) return '';
    try {
        const date = new Date(isoString);
        return date.toLocaleString();
    } catch (e) {
        return isoString;
    }
}

/**
 * Show a toast notification
 */
function showToast(message, type = 'info') {
    // Simple alert for now - could be enhanced with a proper toast system
    alert(message);
}

/**
 * Confirm action
 */
function confirmAction(message) {
    return confirm(message);
}

/**
 * Initialize common functionality
 */
document.addEventListener('DOMContentLoaded', () => {
    // Initialize theme toggle buttons
    document.querySelectorAll('.theme-toggle button').forEach(btn => {
        btn.addEventListener('click', () => {
            const theme = btn.getAttribute('data-theme');
            if (theme) {
                window.themeManager.setTheme(theme);
            }
        });
    });

    // Initialize logout buttons
    document.querySelectorAll('[data-action="logout"]').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            try {
                await fetch('/auth/logout', { method: 'POST' });
                window.location.href = '/login-access';
            } catch (e) {
                window.location.href = '/auth/logout';
            }
        });
    });
});
