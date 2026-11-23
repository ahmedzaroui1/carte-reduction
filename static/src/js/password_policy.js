/** @odoo-module **/

const policyMessage = (
    "Le mot de passe doit contenir au moins 8 caractères, "
    + "une majuscule (A-Z) et un chiffre (0-9)."
);
const passwordSelector = ".oe_signup_form input[name='password'], "
    + ".oe_reset_password_form input[name='password']";

function isValidPassword(value) {
    return value.length >= 8 && /[A-Z]/.test(value) && /[0-9]/.test(value);
}

function configurePasswordFields() {
    document.querySelectorAll(passwordSelector).forEach((input) => {
        input.minLength = 8;
        input.pattern = "(?=.*[A-Z])(?=.*[0-9]).{8,}";
        input.title = policyMessage;
        input.autocomplete = "new-password";
    });
}

function showPolicyError(form) {
    let message = form.querySelector("[data-password-policy-error]");
    if (!message) {
        message = document.createElement("p");
        message.className = "alert alert-danger";
        message.setAttribute("role", "alert");
        message.setAttribute("data-password-policy-error", "");
        form.insertBefore(message, form.querySelector(".oe_login_buttons"));
    }
    message.textContent = policyMessage;
}

document.addEventListener("DOMContentLoaded", configurePasswordFields);
document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)
        || !form.matches(".oe_signup_form, .oe_reset_password_form")) {
        return;
    }

    const password = form.querySelector("input[name='password']");
    if (password && !isValidPassword(password.value)) {
        event.preventDefault();
        showPolicyError(form);
        password.focus();
        password.reportValidity();
    }
});
document.addEventListener("input", (event) => {
    const input = event.target;
    if (input.matches && input.matches(passwordSelector)) {
        const form = input.form;
        if (form && isValidPassword(input.value)) {
            form.querySelector("[data-password-policy-error]")?.remove();
        }
    }
});
