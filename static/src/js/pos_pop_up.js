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
        confirmText: _lt("Confirmer"),
        cancelText: _lt("Fermer"),
        title: _lt("Information"),
        body: _lt("Une action est requise."),
    };

    async confirm() {
        if (this.props.otpMode && (!this.state.remaining || !this.state.otp.trim())) {
            return;
        }
        this.props.resolve(this.props.otpMode
            ? { codeOrQr: this.state.otp.trim() }
            : true);
        this.props.close();
    }

    cancel() {
        this.props.resolve(false);
        this.props.close();
    }
}
