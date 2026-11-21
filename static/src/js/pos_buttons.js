/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { CustomAlertPopup } from "@cartes_reduction/js/pos_pop_up";  // Assuming you've defined the CustomAlertPopup elsewhere
import { markup } from "@odoo/owl";
import { AbstractAwaitablePopup } from "@point_of_sale/app/popup/abstract_awaitable_popup";

patch(PaymentScreen.prototype, {
    async validateOrder(...args) {
        this.currentOrder.set_to_invoice(true);
        return super.validateOrder(...args);
    },
});

export class CreateButton extends Component {
    static template = "point_of_sale.CreateButton";

    /** Setup function to initialize the component. */
    setup() {
        this.pos = usePos();
        this.popup = useService("popup");
    }

    /** Click event handler for the create button. */
    async onClick() {
        const order = this.pos.get_order();
        const client = order.get_partner();

        if (!client) {
            this.popup.add(CustomAlertPopup, {
                title: _t("Aucun client"),
                body: _t("Veuillez d'abord sélectionner un client."),
            });
            return;
        }

        const bestCard = order.best_discount_card;

        if (!bestCard) {
            this.popup.add(CustomAlertPopup, {
                title: _t("Pas de carte optimale"),
                body: _t("Aucune carte optimale n'a été trouvée pour cette commande."),
            });
            return;
        }

        const htmlBestCard = `
            <p><strong>Carte sélectionnée :</strong> ${bestCard.card_name}</p>
            <p><strong>Remise totale :</strong> ${bestCard.total_discount} TND</p>
            <p><strong>Programme utilisé :</strong> ${bestCard.program_used.program_name} — <strong>${bestCard.program_used.reward_type === 'specific' ? bestCard.total_discount + ' TND' : bestCard.program_used.reward_discount + '%'}</strong></p>
            <hr/>
            <p style="font-size: 0.9em; color: #666;">Cliquez sur "Voir toutes les cartes" pour afficher les autres cartes disponibles.</p>
        `;

        // Affiche la popup de la meilleure carte

        this.popup.add(CustomAlertPopup, {
            title: _t("Meilleure carte pour cette commande"),
            body: markup(htmlBestCard),
            confirmText: _t("Voir toutes les cartes"),
            cancelText: _t("Fermer"),
        }).then(
            (confirmed) => {
                if (confirmed) {
                    console.log("Voir toutes les cartes : Déclenchement de showAllCards");
                     this.showAllCards(client);
                } else {
                    console.log("Popup fermé");
                }
            }
        );
    }

    /** Function to display all cards for a client. */
    showAllCards(client) {
        const cartes = this.pos.getCartesByClient(client.id);
        console.log("Cartes de réduction pour le client : ", cartes); // Debugging

        if (cartes.length === 0) {
            this.popup.add(CustomAlertPopup, {
                title: _t("Aucune carte disponible"),
                body: _t("Ce client n'a aucune carte de réduction."),
            });
            return;
        }

        const htmlContent = cartes.map(carte => {
            let programsHtml = carte.programs.map(program => {
                let rewardsHtml = program.rewards.map(reward =>
                    `<li>🎁 ${reward.description} — <strong>${reward.discount}${reward.discount_mode === 'percent' ? '%' : ''}</strong></li>`
                ).join("");
                return `<p><strong>${program.name}</strong><ul>${rewardsHtml}</ul></p>`;
            }).join("");
            return `
                <div style="margin-bottom: 1em;">
                    <p><strong>Carte :</strong> ${carte.name}</p>
                    <p><strong>Attribuée :</strong> ${carte.date_attribution}</p>
                    <p><strong>Expiration :</strong> ${carte.date_expiration}</p>
                    ${programsHtml}
                    <hr/>
                </div>
            `;
        }).join("");

        // Affiche la popup avec toutes les cartes
        this.popup.add(CustomAlertPopup, {
            title: _t("Cartes de réduction"),
            body: markup(htmlContent),
            confirmText: _t("Ok"),
            cancelText: _t("Fermer"),
        });
    }


}

export class VerifyCardOtpButton extends Component {
    static template = "point_of_sale.VerifyCardOtpButton";

    setup() {
        this.pos = usePos();
        this.popup = useService("popup");
        this.orm = useService("orm");
    }

    async onClick() {
        const partner = this.pos.get_order()?.get_partner();
        if (!partner) {
            await this.popup.add(CustomAlertPopup, {
                title: _t("Aucun client"),
                body: _t("Sélectionnez un client avant de vérifier son identité."),
            });
            return;
        }

        try {
            const challenge = await this.orm.call(
                "res.partner",
                "action_send_pos_otp",
                [[partner.id]]
            );
            const countdown = Math.max(
                0,
                challenge.expires_epoch - Math.floor(Date.now() / 1000)
            );
            const codeOrQr = await this.popup.add(CustomAlertPopup, {
                title: _t("Vérification du client"),
                body: _t("Un code de 6 chiffres a été envoyé au client par e-mail."),
                otpMode: true,
                confirmText: _t("Vérifier"),
                countdown,
            });
            if (!codeOrQr) {
                return;
            }
            const result = await this.orm.call(
                "res.partner",
                "verify_pos_otp_or_qr",
                [[partner.id], codeOrQr]
            );
            await this.popup.add(CustomAlertPopup, {
                title: result.success ? _t("Client vérifié") : _t("Échec de vérification"),
                body: result.message,
            });
        } catch (error) {
            await this.popup.add(CustomAlertPopup, {
                title: _t("Vérification impossible"),
                body: error.message || _t("Une erreur est survenue pendant la vérification."),
            });
        }
    }
}

/** Add the OrderlineProductCreateButton component to the control buttons in ProductScreen. */
ProductScreen.addControlButton({
    component: CreateButton,
});
ProductScreen.addControlButton({
    component: VerifyCardOtpButton,
});
