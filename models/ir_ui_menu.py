from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _visible_menu_ids(self, debug=False):
        if self.env.user.login == "caissier.sotufab":
            visible_ids = super()._visible_menu_ids(debug=debug)
            pos_root = self.env.ref("point_of_sale.menu_point_root", raise_if_not_found=False)
            if not pos_root:
                return set()

            visible_pos_ids = set()
            for menu in self.browse(visible_ids):
                parent = menu
                while parent and parent != pos_root:
                    parent = parent.parent_id
                if parent:
                    visible_pos_ids.add(menu.id)
            return visible_pos_ids
        return super()._visible_menu_ids(debug=debug)
