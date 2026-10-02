// static/js/app.js

/* ================================================================
   Dark mode — must run BEFORE paint to avoid FOUC.
   That's why base.html has a tiny inline script in <head>.
   This file handles the toggle behavior at runtime.
   ================================================================ */
(function () {
  const STORAGE_KEY = "theme"; // "light" | "dark" | "system"
  const root = document.documentElement;

  function apply(theme) {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = theme === "dark" || (theme === "system" && prefersDark);
    root.classList.toggle("dark", isDark);
  }

  window.theme = {
    get() {
      return localStorage.getItem(STORAGE_KEY) || "system";
    },
    set(theme) {
      localStorage.setItem(STORAGE_KEY, theme);
      apply(theme);
    },
    toggle() {
      const current = this.get();
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const effective = current === "system"
        ? (prefersDark ? "dark" : "light")
        : current;
      this.set(effective === "dark" ? "light" : "dark");
    },
  };

  // React to OS changes when in "system" mode
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (window.theme.get() === "system") apply("system");
  });
})();

/* ================================================================
   Submit-button guard — prevents double-submits on POST forms.
   Usage: add data-guard-submit to the form.
   ================================================================ */
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!form.matches("form[data-guard-submit]")) return;
  const btn = form.querySelector('button[type="submit"]');
  if (!btn) return;
  btn.disabled = true;
  btn.dataset.originalText = btn.textContent;
  btn.textContent = btn.dataset.loadingText || "Working…";
});

/* ================================================================
   Confirm-before-delete — replaces JS confirm() with a nicer flow.
   Usage: <form data-confirm="Delete this item?">
   ================================================================ */
document.addEventListener("submit", (e) => {
  const form = e.target;
  const msg = form.dataset.confirm;
  if (!msg) return;
  if (!window.confirm(msg)) {
    e.preventDefault();
    // Re-enable any disabled buttons
    form.querySelectorAll('button[type="submit"]').forEach((b) => {
      b.disabled = false;
    });
  }
});

/* ================================================================
   HTMX — global config
   ================================================================ */
document.addEventListener("DOMContentLoaded", () => {
  if (!window.htmx) return;

  // Send CSRF token with every HTMX request
  document.body.addEventListener("htmx:configRequest", (e) => {
    const token = document.querySelector("meta[name=csrf-token]")?.content;
    if (token) e.detail.headers["X-CSRFToken"] = token;
  });

  // Flash messages from HX-Trigger headers
  document.body.addEventListener("showMessage", (e) => {
    window.dispatchEvent(new CustomEvent("app:message", { detail: e.detail }));
  });
});

/* ================================================================
   Alpine — global stores
   ================================================================ */
document.addEventListener("alpine:init", () => {
  // Toast store
  Alpine.store("toasts", {
    items: [],
    push(message, type = "info", timeout = 4000) {
      const id = Date.now() + Math.random();
      this.items.push({ id, message, type });
      setTimeout(() => this.remove(id), timeout);
    },
    remove(id) {
      this.items = this.items.filter((t) => t.id !== id);
    },
  });

  // Listen for global message events
  window.addEventListener("app:message", (e) => {
    Alpine.store("toasts").push(e.detail.message, e.detail.level);
  });
});

/* ================================================================
   POS (Point of Sale) — Alpine component
   Registered on window.posApp so it's available when Alpine
   initializes. In templates, use x-data="posApp()".
   ================================================================ */
window.posApp = function posApp() {
  return {
    // ---- State ----
    products: [],
    cart: [],
    search: "",
    mobileCartOpen: false,
    checkoutOpen: false,
    submitting: false,
    paymentMethod: "cash",
    cashPaid: 0,
    discount: 0,
    taxRate: 0,

    // ---- Init ----
    init() {
      // Load products from json_script
      const el = document.getElementById("products-data");
      try {
        this.products = el ? JSON.parse(el.textContent) : [];
        if (!Array.isArray(this.products)) this.products = [];
      } catch (e) {
        console.error("[POS] Failed to parse products:", e);
        this.products = [];
      }

      // Restore cart if the last POST failed
      const cartEl = document.getElementById("initial-cart-data");
      if (cartEl) {
        try {
          const restored = JSON.parse(cartEl.textContent);
          if (Array.isArray(restored) && restored.length > 0) {
            this.cart = restored.map((item) => {
              const p = this.products.find((x) => x.id === item.product_id);
              return {
                product_id: item.product_id,
                name: p ? p.name : "Unknown product",
                unit_price: parseFloat(item.unit_price),
                quantity: item.quantity,
                max_stock: p ? p.quantity : Infinity,
              };
            });
          }
        } catch (e) {
          console.warn("[POS] Could not restore cart:", e);
        }
      }

      // Tax rate from data attribute
      const taxEl = document.getElementById("pos-tax-rate");
      this.taxRate = taxEl ? parseFloat(taxEl.dataset.taxRate) || 0 : 0;

      // Wire payment method select
      const pm = document.getElementById("id_payment_method");
      if (pm) {
        this.paymentMethod = pm.value || "cash";
        pm.addEventListener("change", (e) => {
          this.paymentMethod = e.target.value;
        });
      }

      // Wire cash input
      const ap = document.getElementById("id_amount_paid");
      if (ap) {
        this.cashPaid = parseFloat(ap.value) || 0;
        ap.addEventListener("input", (e) => {
          this.cashPaid = parseFloat(e.target.value) || 0;
        });
      }

      // Wire discount input
      const di = document.getElementById("id_discount");
      if (di) {
        this.discount = parseFloat(di.value) || 0;
        di.addEventListener("input", (e) => {
          this.discount = parseFloat(e.target.value) || 0;
        });
      }

      // Keyboard: `/` focuses search
      window.addEventListener("keydown", (e) => {
        if (
          e.key === "/" &&
          document.activeElement.tagName !== "INPUT" &&
          document.activeElement.tagName !== "TEXTAREA"
        ) {
          e.preventDefault();
          document.getElementById("pos-search")?.focus();
        }
      });

      // Enter in search → add first match
      document.getElementById("pos-search")?.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        const q = this.search.trim();
        if (!q) return;

        const exact = this.products.find(
          (p) => (p.sku || "").toLowerCase() === q.toLowerCase()
        );
        if (exact) {
          this.addToCart(exact);
          this.search = "";
          return;
        }

        const matches = this.filteredProducts;
        if (matches.length === 1) {
          this.addToCart(matches[0]);
          this.search = "";
        }
      });

      // Global Enter → checkout
      window.addEventListener("keydown", (e) => {
        if (
          e.key === "Enter" &&
          !this.checkoutOpen &&
          this.cart.length > 0 &&
          document.activeElement.tagName !== "INPUT" &&
          document.activeElement.tagName !== "TEXTAREA"
        ) {
          this.openCheckout();
        }
      });
    },

    // ---- Computed (getters) ----
    get filteredProducts() {
      const q = (this.search || "").toLowerCase().trim();
      const list = this.products || [];
      if (!q) return list;
      return list.filter(
        (p) =>
          (p.name || "").toLowerCase().includes(q) ||
          (p.sku || "").toLowerCase().includes(q)
      );
    },

    get subtotal() {
      return this.cart.reduce((s, i) => s + i.quantity * i.unit_price, 0);
    },

    get tax() {
      return this.subtotal * (this.taxRate / 100);
    },

    get total() {
      return Math.max(0, this.subtotal + this.tax - this.discount);
    },

    get cartCount() {
      return this.cart.reduce((s, i) => s + i.quantity, 0);
    },

    get changeDue() {
      return Math.max(0, this.cashPaid - this.total);
    },

    // ---- Cart actions ----
    addToCart(product) {
      if (!product || product.quantity <= 0) return;
      const existing = this.cart.find((i) => i.product_id === product.id);
      if (existing) {
        if (existing.quantity >= product.quantity) {
          this.toast(`Only ${product.quantity} in stock`, "warning");
          return;
        }
        existing.quantity += 1;
      } else {
        this.cart.push({
          product_id: product.id,
          name: product.name,
          unit_price: parseFloat(product.selling_price),
          quantity: 1,
          max_stock: product.quantity,
        });
      }
    },

    increment(idx) {
      const item = this.cart[idx];
      if (!item) return;
      if (item.quantity >= (item.max_stock || Infinity)) {
        this.toast(`Only ${item.max_stock} in stock`, "warning");
        return;
      }
      item.quantity += 1;
    },

    decrement(idx) {
      const item = this.cart[idx];
      if (!item) return;
      if (item.quantity <= 1) {
        this.removeItem(idx);
      } else {
        item.quantity -= 1;
      }
    },

    setQty(idx, value) {
      const item = this.cart[idx];
      if (!item) return;
      const q = Math.max(1, parseInt(value, 10) || 1);
      item.quantity = Math.min(q, item.max_stock || q);
    },

    removeItem(idx) {
      this.cart.splice(idx, 1);
    },

    confirmClear() {
      if (confirm("Clear the cart?")) {
        this.cart = [];
      }
    },

    // ---- Checkout ----
    openCheckout() {
      if (this.cart.length === 0) return;
      this.mobileCartOpen = false;
      this.checkoutOpen = true;
      this.syncCartInput();
    },

    syncCartInput() {
      const ci = document.getElementById("id_cart");
      if (!ci) return;
      ci.value = JSON.stringify(
        this.cart.map((i) => ({
          product_id: i.product_id,
          quantity: i.quantity,
          unit_price: i.unit_price,
        }))
      );
    },

    submitSale(e) {
      this.syncCartInput();

      if (this.cart.length === 0) {
        e.preventDefault();
        this.toast("Cart is empty", "error");
        return;
      }
      if (this.paymentMethod === "cash" && this.cashPaid < this.total) {
        e.preventDefault();
        this.toast("Cash tendered is less than the total", "error");
        return;
      }

      this.submitting = true;
    },

    // ---- Helpers ----
    fmtMoney(n) {
      return Number(n || 0).toFixed(2);
    },

    toast(message, type = "info") {
      if (window.Alpine?.store("toasts")) {
        window.Alpine.store("toasts").push(message, type);
      } else {
        alert(message);
      }
    },
  };
};