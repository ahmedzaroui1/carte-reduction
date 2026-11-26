/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const BLOCKED_EVENT_TYPES = [
    "replay_blocked",
    "velocity_abuse",
    "unauthorized_access",
    "tampered_token",
    "ml_integrity_failure",
    "pos_otp_failed",
    "pin_failed",
];

class AdminSecurityDashboard extends Component {
    static template = "cartes_reduction.AdminSecurityDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            activeCards: 0,
            posValidations: 0,
            blockedAlerts: 0,
            loading: true,
            loaded: false,
            error: "",
        });
        this.isLoading = false;
        this.refreshTimer = null;
        onWillStart(() => this.loadStats());
        onWillUnmount(() => clearInterval(this.refreshTimer));
        this.refreshTimer = setInterval(() => this.loadStats(), 30000);
    }

    async loadStats() {
        if (this.isLoading) {
            return;
        }
        this.isLoading = true;
        this.state.loading = !this.state.loaded;
        this.state.error = "";
        const now = new Date();
        const today = [
            now.getFullYear(),
            String(now.getMonth() + 1).padStart(2, "0"),
            String(now.getDate()).padStart(2, "0"),
        ].join("-");

        try {
            const [activeCards, posValidations, blockedAlerts] = await Promise.all([
                this.orm.searchCount("cartes.attribution", [
                    "&",
                    ["state", "in", ["validated", "renouvelé"]],
                    "&",
                    "|",
                    ["date_expiration", "=", false],
                    ["date_expiration", ">=", today],
                    ["carte_ids.active", "=", true],
                ]),
                this.orm.searchCount("cartes.reduction.security.audit", [
                    ["event_type", "=", "redemption"],
                ]),
                this.orm.searchCount("cartes.reduction.security.audit", [
                    ["event_type", "in", BLOCKED_EVENT_TYPES],
                ]),
            ]);
            this.state.activeCards = activeCards;
            this.state.posValidations = posValidations;
            this.state.blockedAlerts = blockedAlerts;
            this.state.loaded = true;
        } catch (error) {
            console.error("Unable to load Zero-Trust dashboard counters.", error);
            this.state.error =
                error.message || "Impossible de charger les compteurs de sécurité.";
        } finally {
            this.isLoading = false;
            this.state.loading = false;
        }
    }

    verifyIntegrity() {
        return this.action.doAction("cartes_reduction.action_verify_audit_integrity");
    }
}

registry
    .category("actions")
    .add("cartes_reduction.security_dashboard", AdminSecurityDashboard);
