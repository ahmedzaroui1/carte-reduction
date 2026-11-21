/** @odoo-module */
import { AbstractAwaitablePopup } from "@point_of_sale/app/popup/abstract_awaitable_popup";
import { _lt } from "@web/core/l10n/translation";
import { onMounted, onWillUnmount, useState } from "@odoo/owl";

/**
 * CustomAlertPopup component for displaying custom messages as an alert popup.
 */
export class CustomAlertPopup extends AbstractAwaitablePopup {
    static template = "pos_buttons.CustomAlertPopup";

    setup() {
        super.setup();
        this.state = useState({
            otp: "",
            remaining: this.props.countdown || 0,
        });
        onMounted(() => {
            if (this.props.otpMode) {
                this.countdownTimer = setInterval(() => {
                    this.state.remaining = Math.max(0, this.state.remaining - 1);
                    if (!this.state.remaining) {
                        clearInterval(this.countdownTimer);
                    }
                }, 1000);
            }
        });
        onWillUnmount(() => clearInterval(this.countdownTimer));
    }

    static defaultProps = {
        confirmText: _lt("Ok"),
        cancelText: _lt("Cancel"),
        title: _lt("Alert"),
        body: _lt("Something happened"),
    };

    // Quand on clique sur le bouton "Ok"
    async confirm() {
        if (this.props.otpMode && !this.state.remaining) {
            this.props.resolve(false);
            this.props.close();
            return;
        }
        this.props.resolve(this.props.otpMode ? this.state.otp.trim() : true);

        this.props.close();
    }

    // Quand on clique sur "Cancel" ou fermeture
    cancel() {
        this.props.resolve(false);    // exécute la fonction de rejet si définie
        this.props.close();       // ferme la popup
    }
}
