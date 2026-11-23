/** @odoo-module **/

const initializeOtpCountdowns = () => {
    for (const element of document.querySelectorAll("[data-otp-countdown]")) {
        const expiresAt = Number(element.dataset.expires);
        let timer;
        const updateCountdown = () => {
            const remaining = Math.max(0, expiresAt - Math.floor(Date.now() / 1000));
            element.textContent = String(remaining);
            if (!remaining) {
                element.closest(".card")?.classList.add("border-danger");
                clearInterval(timer);
            }
        };
        updateCountdown();
        if (expiresAt > Math.floor(Date.now() / 1000)) {
            timer = setInterval(updateCountdown, 1000);
        }
    }
};

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeOtpCountdowns, { once: true });
} else {
    initializeOtpCountdowns();
}
