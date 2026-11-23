/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { Order, Orderline } from "@point_of_sale/app/store/models";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { CustomAlertPopup } from "@cartes_reduction/js/pos_pop_up";

async function verifyOrderCustomer(
    screen,
    { order = screen.pos.get_order(), forceOtp = false, showResult = false } = {}
) {
    const partner = order?.get_partner();
    if (!partner) {
        await screen.popup.add(CustomAlertPopup, {
            title: _t("Aucun client"),
            body: _t("Sélectionnez un client avant de vérifier son identité."),
        });
        return false;
    }

    const card = screen.pos.getHighestTierCardByClient(partner.id);
    if (!card && !forceOtp) return true;
    if (order.cardDiscountVerifiedPartnerId === partner.id) {
        order._applyVerifiedCardDiscount?.();
        if (showResult) {
            await screen.popup.add(CustomAlertPopup, {
                title: _t("Client déjà vérifié"),
                body: card
                    ? _t("La remise de la carte est déjà appliquée à cette commande.")
                    : _t("Le client est déjà vérifié pour cette commande."),
            });
        }
        return true;
    }

    try {
        const challenge = await screen.orm.call(
            "res.partner",
            "action_send_pos_otp",
            [[partner.id], order.uid, card?.id || false]
        );
        const countdown = Math.max(
            0,
            challenge.expires_epoch - Math.floor(Date.now() / 1000)
        );
        const codeOrQr = await screen.popup.add(CustomAlertPopup, {
            title: _t("Vérification du client"),
            body: _t("Un code de 6 chiffres ou un QR signé a été envoyé au client par e-mail."),
            otpMode: true,
            confirmText: _t("Vérifier"),
            countdown,
        });
        if (!codeOrQr || !codeOrQr.codeOrQr) return false;

        const result = await screen.orm.call(
            "res.partner",
            "verify_pos_otp_or_qr",
            [
                [partner.id],
                codeOrQr.codeOrQr,
                order.uid,
                card?.id || false,
            ]
        );
        if (!result.success) {
            await screen.popup.add(CustomAlertPopup, {
                title: _t("Échec de vérification"),
                body: result.message,
            });
            return false;
        }

        order.cardDiscountVerifiedPartnerId = partner.id;
        order.cardDiscountVerifiedCardId = card?.id || null;
        order._applyVerifiedCardDiscount?.();
        if (showResult) {
            await screen.popup.add(CustomAlertPopup, {
                title: _t("Client vérifié"),
                body: card
                    ? _t("La remise de la carte a été appliquée à la commande.")
                    : result.message,
            });
        }
        return true;
    } catch (error) {
        await screen.popup.add(CustomAlertPopup, {
            title: _t("Vérification impossible"),
            body: error.message || _t("Une erreur est survenue pendant la vérification."),
        });
        return false;
    }
}

patch(PaymentScreen.prototype, {
    async validateOrder(...args) {
        this.currentOrder.set_to_invoice(true);
        return super.validateOrder(...args);
    },
});

patch(ProductScreen.prototype, {
    get cardDiscountPreview() {
        const order = this.pos.get_order();
        const partner = order?.get_partner();
        const card = partner && this.pos.getHighestTierCardByClient(partner.id);
        if (!card) return null;

        const discount = Math.min(100, Math.max(0, Number(card.discount) || 0));
        const currentTotal = order.get_total_with_tax();
        const isVerified = order.cardDiscountVerifiedPartnerId === partner.id;
        const originalTotal = isVerified && discount < 100
            ? currentTotal / (1 - discount / 100)
            : currentTotal;
        return {
            tier: card.carte_type.charAt(0).toUpperCase() + card.carte_type.slice(1),
            discount,
            points: this.pos.getLoyaltyPointsByClient(partner.id),
            originalTotal,
            discountedTotal: originalTotal * (1 - discount / 100),
        };
    },

});

patch(Order.prototype, {
    export_as_JSON(...args) {
        const json = super.export_as_JSON(...args);
        json.card_redemption_order_uid = this.uid;
        return json;
    },

    async pay(...args) {
        const partner = this.get_partner();
        const pos = this.env.services.pos;
        const card = partner && pos.getHighestTierCardByClient(partner.id);
        if (card) {
            const verified = await verifyOrderCustomer({
                pos,
                orm: this.env.services.orm,
                popup: this.env.services.popup,
            }, { order: this });
            if (!verified) return;
        }
        return super.pay(...args);
    },
});

patch(Orderline.prototype, {
    export_as_JSON(...args) {
        const json = super.export_as_JSON(...args);
        const cardId = this.order?.cardDiscountVerifiedCardId;
        if (cardId && this.discount > 0) {
            json.custom_discount_card_id = cardId;
        }
        return json;
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

        const rewardAmount = bestCard.program_used.reward_type === "specific"
            ? `${bestCard.total_discount} TND`
            : `${bestCard.program_used.reward_discount}%`;
        const body = [
            `${_t("Carte sélectionnée")} : ${bestCard.card_name}`,
            `${_t("Remise totale")} : ${bestCard.total_discount} TND`,
            `${_t("Programme utilisé")} : ${bestCard.program_used.program_name} — ${rewardAmount}`,
            _t('Cliquez sur "Voir toutes les cartes" pour afficher les autres cartes disponibles.'),
        ].join("\n");

        this.popup.add(CustomAlertPopup, {
            title: _t("Meilleure carte pour cette commande"),
            body,
            confirmText: _t("Voir toutes les cartes"),
            cancelText: _t("Fermer"),
        }).then(
            (confirmed) => {
                if (confirmed) {
                    this.showAllCards(client);
                }
            }
        );
    }

    /** Function to display all cards for a client. */
    showAllCards(client) {
        const cartes = this.pos.getCartesByClient(client.id);
        if (cartes.length === 0) {
            this.popup.add(CustomAlertPopup, {
                title: _t("Aucune carte disponible"),
                body: _t("Ce client n'a aucune carte de réduction."),
            });
            return;
        }

        const body = cartes.map(carte => {
            const programDetails = carte.programs.flatMap(program => [
                program.name,
                ...program.rewards.map(reward => (
                    `- ${reward.description} — ${reward.discount}${reward.discount_mode === "percent" ? "%" : ""}`
                )),
            ]);
            return [
                `${_t("Carte")} : ${carte.name}`,
                `${_t("Attribuée")} : ${carte.date_attribution}`,
                `${_t("Expiration")} : ${carte.date_expiration}`,
                ...programDetails,
            ].join("\n");
        }).join("\n\n");

        this.popup.add(CustomAlertPopup, {
            title: _t("Cartes de réduction"),
            body,
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
        await verifyOrderCustomer(this, { forceOtp: true, showResult: true });
    }
}

/** Add the OrderlineProductCreateButton component to the control buttons in ProductScreen. */
ProductScreen.addControlButton({
    component: CreateButton,
});
ProductScreen.addControlButton({
    component: VerifyCardOtpButton,
});
