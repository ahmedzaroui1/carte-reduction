/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";
import { Order } from "@point_of_sale/app/store/models";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";


const originalSetPartner = Order.prototype.set_partner;
const originalAddProduct = Order.prototype.add_product;
const originalRemoveOrderline = Order.prototype.remove_orderline;

// 1. Patch critique pour bloquer TOUTES les remises automatiques


patch(Order.prototype, {
    set_partner(partner) {
        originalSetPartner.call(this, partner);

        if (partner) {
            console.log("Client sélectionné :", partner.name, "| ID :", partner.id);

            const pos = this.pos;

            if (!pos) {
                console.error("❌ pos est undefined dans Order");
                return;
            }

            this._recomputeDiscounts();
        } else {
            this._clearExistingDiscounts();
            this.best_discount_card = null;
            console.log("Client désélectionné.");
        }
    },
});



patch(Order.prototype, {

    remove_orderline(orderline) {
        const result = originalRemoveOrderline.call(this, orderline);
        this._recomputeDiscounts();
        return result;
    },

    _recomputeDiscounts() {
        const client = this.get_partner();
        if (!client || !this.pos) return;
            // 1. Si un programme est forcé (via suggestion), l'utiliser directement
        if (this.best_discount_card?.is_forced) {
            console.log("💳 Application de la carte forcée :", this.best_discount_card.name);
            this._applyDiscounts(this.best_discount_card);
            return;
        }

        // 1. Récupération des cartes valides
        const cartes = this.pos.getCartesByClient(client.id) || [];
        const bestCard = this.pos.getBestDiscountCard?.(cartes, this);

        // 2. Nettoyage des anciennes remises
        this._clearExistingDiscounts();

        // 3. Application des nouvelles remises
        if (bestCard) {
            this.best_discount_card = bestCard;
            console.log("💳 Application de la carte :", bestCard.card_name);
            this._applyDiscounts(bestCard);
        } else {
            this.best_discount_card = null;
            console.log("🔙 Réinitialisation des remises");
        }
    },

    _clearExistingDiscounts() {
        // Supprime les lignes de remise existantes
        this.get_orderlines()
            .filter(line => line.is_reward_line)
            .forEach(line => originalRemoveOrderline.call(this, line));

        // Réinitialise les remises sur les produits
        this.get_orderlines()
            .filter(line => !line.is_reward_line)
            .forEach(line => line.set_discount(0));
    },

    _applyDiscounts(bestCard) {
        const program = bestCard.program_used;

        if (program.reward_type === 'order') {
            // Cas 1: Remise globale
            console.log("🔵 Application remise globale :", program.reward_discount + "%");
            this._applyGlobalDiscount(program.reward_discount);

        } else if (program.reward_type === 'specific') {
            // Cas 2: Remise spécifique par produit
            console.log("🟢 Application remises spécifiques");
            this._applySpecificDiscounts(program.product_discounts);
        }
    },

    _applyGlobalDiscount(discountValue) {
        this.get_orderlines()
            .filter(line => !line.is_reward_line)
            .forEach(line => line.set_discount(discountValue));
    },

    _applySpecificDiscounts(productDiscounts) {
        Object.entries(productDiscounts || {}).forEach(([productId, discountData]) => {
            const discountValue = discountData.reward.discount;
            this.get_orderlines()
                .filter(line => line.product.id === parseInt(productId))
                .forEach(line => line.set_discount(discountValue));
        });
    },


    async add_product(product, options) {
        const result = await super.add_product(...arguments);

        const client = this.get_partner();
        if (!client) return result;

        // Supprimer toutes les lignes de remises automatiques
        this.orderlines
            .filter((line) => line.get_unit_price() < 0 && line.product?.is_reward_line)
            .forEach((line) => this.remove_orderline(line));

        // Réinitialiser toutes les remises
        this.get_orderlines().forEach(line => {
            line.set_discount(0);
        });

        // Calculer la meilleure carte
        const cartes = this.pos.getCartesByClient(client.id);
        const bestCard = this.pos.getBestDiscountCard(cartes, this);
        this.best_discount_card = bestCard;

        // Appliquer les remises
        if (bestCard?.program_used) {
            if (bestCard.program_used.reward_type === 'order') {
                // Remise globale
                const discountValue = parseFloat(bestCard.program_used.reward_discount);
                this.get_orderlines().forEach(line => {
                    line.set_discount(discountValue);
                });
            } else if (bestCard.program_used.reward_type === 'specific') {
                // Remises spécifiques
                for (const [productId, { reward }] of Object.entries(bestCard.program_used.product_discounts || {})) {
                    const discountValue = reward.discount;
                    this.get_orderlines()
                        .filter(line => line.product.id === parseInt(productId))
                        .forEach(line => {
                            line.set_discount(discountValue);
                        });
                }
            }
        }
        this._checkProductSuggestions();
        return result;
    },
    async _checkProductSuggestions() {
        const order = this;
        const pos = this.pos;
        const client = order.get_partner();
        if (!client) return;

        const ordered_product_ids = order.get_orderlines().map(line => line.product.id);
        const rules = pos.product_association_rules || [];

        for (const rule of rules) {
            const antecedent_ids = rule.antecedent_product_ids;
            const suggested_id = rule.associated_product_id;
            const card = rule.card_id;

            const hasAllAntecedents = antecedent_ids.every(pid => ordered_product_ids.includes(pid));
            const alreadyHasSuggested = ordered_product_ids.includes(suggested_id[0]);

            if (hasAllAntecedents && !alreadyHasSuggested) {
                const suggested_name = suggested_id[1];
                const card_name = card ? card[1] : 'carte de réduction';
                const card_id = card ? card[0] : null;

                const confirmation = window.confirm(
                    `Ajoutez "${suggested_name}" pour bénéficier de la remise "${card_name}" ?`
                );

                if (confirmation) {
                    // 1. Ajouter le produit suggéré
                    const productToAdd = pos.db.get_product_by_id(suggested_id[0]);
                    if (productToAdd) {
                        await order.add_product(productToAdd);
                    }

                    // 2. Appliquer le programme sans attribution
                    if (card_id) {
                        const carteReduction = pos.cartes_reduction.find(c => c.id === card_id);
                        if (carteReduction?.loyalty_program_id?.length) {
                            const program = pos.loyalty_programs.find(
                                p => p.id === carteReduction.loyalty_program_id[0]
                            );

                            if (program && program.rewards?.length) {
                                const reward = program.rewards[0];
                                console.log(`Application temporaire du programme: ${program.name}`);

                                // Construction de l'objet discount directement à partir du programme
                                const discountData = {
                                    reward_type: reward.reward_type,
                                    reward_discount: reward.discount,
                                    discount_applicability: reward.discount_applicability,
                                    discount_product_ids: reward.discount_product_ids || [],
                                    product_discounts: reward.discount_applicability === 'specific'
                                        ? reward.discount_product_ids.reduce((acc, pid) => {
                                            acc[pid] = { reward: reward };
                                            return acc;
                                        }, {})
                                        : null
                                };

                                // Appliquer directement les remises
                                order._clearExistingDiscounts();

                                if (discountData.discount_applicability === 'order') {
                                    order._applyGlobalDiscount(discountData.reward_discount);
                                } else if (discountData.discount_applicability === 'specific') {
                                    order._applySpecificDiscounts(discountData.product_discounts);
                                }


                            }
                        }
                    }
                }
                break;
            }
        }
    },




    _applyBestDiscount() {
        const client = this.get_partner();
        if (!client) {
            console.log("Aucun client sélectionné - annulation du recalcul");
            return;
        }

        console.log("🔍 Recherche de la meilleure remise...");

        // 1. Supprimer les anciennes remises
        this.get_orderlines()
            .filter(line => line.is_reward_line)
            .forEach(line => originalRemoveOrderline.call(this, line));

        // 2. Réappliquer les remises
        const pos = this.pos;
        const cartes = pos.getCartesByClient(client.id);
        const bestCard = pos.getBestDiscountCard(cartes, this);

        if (bestCard?.program_used) {
            console.log("💳 Application de la remise :", bestCard.card_name);
            const { reward_type, reward_discount } = bestCard.program_used;

            if (reward_type === 'order') {
                this.get_orderlines()
                    .filter(line => !line.is_reward_line)
                    .forEach(line => line.set_discount(reward_discount));
            }
            // Ajouter ici la logique pour les remises spécifiques si nécessaire
        }
    },
        // Patch les méthodes de récompense pour les désactiver automatiquement
    _applyReward(reward, coupon_id, args) {
        console.log("[CUSTOM] Application auto de récompense bloquée");
        return false;
    },

    _applyProgramReward(program, reward) {
        console.log("[CUSTOM] Programme auto bloqué :", program.name);
        return false;
    },

    _createDiscountLine(reward, price) {
        console.log("[CUSTOM] Création ligne de remise bloquée");
        return null;
    },

    // Nouvelle méthode pour recalculer la remise quand la commande change
    recalculate_discount() {
        const client = this.get_partner();
        if (!client) return;

        const cartes = this.pos.getCartesByClient(client.id);
        const bestCard = this.pos.getBestDiscountCard(cartes, this);
        this.best_discount_card = bestCard;

        if (bestCard?.program_used) {
            const { reward_type, reward_discount } = bestCard.program_used;
            const discountValue = parseFloat(reward_discount);

            if (reward_type === 'order') {
                this.get_orderlines().forEach(line => {
                    line.set_discount(discountValue);
                });
            }
            // Mise à jour de l'affichage
            const discountElement = document.querySelector('.pos-discount');
            if (discountElement) {
                discountElement.innerText = `Remise ${bestCard.card_name}: ${discountValue}%`;
            }
        }
    }
});
// Enregistrement dans le système Odoo 17
registry.category("pos.models").add("Order", Order, { force: true });
// PATCH de la classe PosStore



patch(PosStore.prototype, {
    async _processData(loadedData) {
        await super._processData(...arguments);

        this.cartes_attribution = loadedData['cartes.attribution'] || [];
        this.cartes_reduction = loadedData['cartes.reduction'] || [];
        this.loyalty_programs = loadedData['loyalty.program'] || [];
        this.loyalty_rules = loadedData['loyalty.rule'] || [];
        this.loyalty_rewards = loadedData['loyalty.reward'] || [];
        this.product_association_rules = loadedData['product.association'] || [];
        console.log("Attributions de cartes :", this.cartes_attribution);
        console.log("Cartes de réduction :", this.cartes_reduction);
        console.log("Programmes de fidélité :", this.loyalty_programs);
        console.log("Product rule :", this.product_association_rules);
    },

    getCartesByClient(clientId) {
        console.log("Recherche des cartes pour le client ID:", clientId);
        const now = new Date();
        const today = [
            now.getFullYear(),
            String(now.getMonth() + 1).padStart(2, '0'),
            String(now.getDate()).padStart(2, '0'),
        ].join('-');
        const dateText = value => {
            if (!value) return null;
            if (value.ts !== undefined) {
                return new Date(value.ts).toISOString().slice(0, 10);
            }
            if (value instanceof Date) return value.toISOString().slice(0, 10);
            return String(value).slice(0, 10);
        };
        const relationId = value => Array.isArray(value) ? value[0] : value;
        const relationIds = value => (value || []).map(item =>
            Array.isArray(item) ? item[0] : item
        );

        const cartes = this.cartes_attribution
            .filter(attr => {
                if (!attr.client_id || attr.client_id[0] !== clientId) return false;
                if (!attr.date_expiration) return true;
                return dateText(attr.date_expiration) >= today;
            })
            .flatMap(attr => {
                return (attr.carte_ids || []).map(carteId => {
                    const carte = this.cartes_reduction.find(c => c.id === carteId);
                    if (!carte) return null;

                    const programIds = relationIds(carte.loyalty_program_id);
                    const programmes = programIds.length
                        ? this.loyalty_programs
                            .filter(p => programIds.includes(p.id))
                            .filter(p => {
                                const start = dateText(p.date_from);
                                const end = dateText(p.date_to);
                                return (!start || start <= today) &&
                                    (!end || today <= end);
                            })
                            .map(program => {
                                return {
                                    ...program,
                                    rewards: this.loyalty_rewards.filter(reward =>
                                        relationId(reward.program_id) === program.id
                                    ),
                                    rules: this.loyalty_rules.filter(rule =>
                                        relationId(rule.program_id) === program.id
                                    ),
                                };
                            })
                        : [];

                    return programmes.length > 0 ? {
                        id: carte.id,
                        name: carte.name,
                        date_attribution: attr.date_attribution,
                        date_expiration: attr.date_expiration,
                        programs: programmes
                    } : null;
                });
            })
            .filter(carte => carte !== null);

        console.log("🟢 Cartes filtrées (programmes valides) :", cartes);
        return cartes;
    },

    getBestDiscountCard(cards, order) {
        if (!order || !order.get_orderlines) return null;

        const orderLines = order.get_orderlines();
        const totalWithTax = order.get_total_with_tax?.() || 0;
        let bestCard = null;
        let maxDiscount = 0;
        let bestCardDebug = null;

        for (const card of cards || []) {
            const bestDiscountsPerProduct = {}; // { productId: { discount, reward, program } }
            let totalGlobalDiscount = 0;
            let bestGlobalProgram = null; // Pour stocker le programme de la meilleure remise globale

            for (const program of card.programs || []) {
                let totalQty = orderLines.reduce((sum, line) => sum + (line.quantity || 0), 0);
                const rules = program.rules?.length ? program.rules : [null];
                const passesRule = rules.some(rule => {
                    const minimumAmount = rule?.minimum_amount || 0;
                    const minimumQty = parseInt(rule?.minimum_qty || 0);
                    const validProductIds = new Set(rule?.valid_product_ids || []);
                    const matchesProduct = rule?.any_product ||
                        validProductIds.size === 0 ||
                        orderLines.some(line => validProductIds.has(line.product?.id));

                    return totalWithTax >= minimumAmount &&
                        totalQty >= minimumQty &&
                        matchesProduct;
                });

                if (!passesRule) {
                    continue;
                }

                for (const reward of program.rewards || []) {
                    if (reward.reward_type !== 'discount') continue;

                    const discountType = reward.discount_applicability;
                    const discountValue = reward.discount || 0;
                    const discountMode = (reward.discount_mode || 'percent').toLowerCase();

                    if (discountType === 'order') {
                        const value = (discountMode === 'percent')
                            ? totalWithTax * (discountValue / 100)
                            : discountValue;
                        if (value > totalGlobalDiscount) {
                            totalGlobalDiscount = value;
                            bestGlobalProgram = program; // Stocke le programme associé
                        }
                    }

                    if (discountType === 'specific') {
                        const productSet = new Set(reward.all_discount_product_ids || []);
                        for (const line of orderLines) {
                            const productId = line.product?.id;
                            if (productId && productSet.has(productId)) {
                                const lineTotal = line.get_price_with_tax?.() || 0;
                                const discountAmount = (discountMode === 'percent')
                                    ? lineTotal * (discountValue / 100)
                                    : discountValue;

                                if (!bestDiscountsPerProduct[productId] || discountAmount > bestDiscountsPerProduct[productId].discount) {
                                    bestDiscountsPerProduct[productId] = {
                                        discount: discountAmount,
                                        reward: reward,
                                        program: program // Stocke le programme associé
                                    };
                                }
                            }
                        }
                    }
                }
            }

            const totalSpecificDiscount = Object.values(bestDiscountsPerProduct)
                .reduce((sum, { discount }) => sum + discount, 0);

            const bestDiscountForCard = Math.max(totalSpecificDiscount, totalGlobalDiscount);

            if (bestDiscountForCard > maxDiscount) {
                maxDiscount = bestDiscountForCard;
                bestCard = card;

                const isSpecificBetter = totalSpecificDiscount >= totalGlobalDiscount;
                const usedProgram = isSpecificBetter
                    ? Object.values(bestDiscountsPerProduct)[0]?.program // Prend le premier programme trouvé
                    : bestGlobalProgram;

                bestCardDebug = {
                    card_id: card.id,
                    card_name: card.name,
                    total_discount: bestDiscountForCard.toFixed(2),
                    program_used: {
                        reward_type: isSpecificBetter ? 'specific' : 'order',
                        reward_discount: isSpecificBetter ? 'Multiple' : (totalGlobalDiscount / totalWithTax * 100).toFixed(2),
                        program_name: usedProgram?.name || 'Aucun programme', // Nom du programme ajouté ici
                        program_id: usedProgram?.id,
                        product_discounts: isSpecificBetter ? bestDiscountsPerProduct : null,
                        rewards: isSpecificBetter
                            ? Object.values(bestDiscountsPerProduct).map(({ reward }) => reward)
                            : []
                    },
                    detail: {
                        total_specific_discount: totalSpecificDiscount.toFixed(2),
                        total_global_discount: totalGlobalDiscount.toFixed(2),
                        used_strategy: isSpecificBetter ? 'specific' : 'global',
                        per_product_discounts: bestDiscountsPerProduct
                    }
                };
            }
        }

        console.log("💳 Meilleure carte pour cette commande :", bestCardDebug);
        return bestCardDebug;
    }
});
