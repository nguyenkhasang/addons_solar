/** @odoo-module **/

import { registerThreadAction } from "@mail/core/common/thread_actions";
import { fields } from "@mail/core/common/record";
import { Thread } from "@mail/core/common/thread_model";

import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    setup() {
        super.setup();
        this.is_smartsolar_ai_chat = fields.Attr(false);
    },
});

registerThreadAction("clear-smartsolar-ai-history", {
    condition: ({ owner, thread }) =>
        Boolean(
            thread?.model === "discuss.channel" &&
                thread.is_smartsolar_ai_chat &&
                !owner.isDiscussSidebarChannelActions
        ),
    icon: "fa fa-fw fa-trash",
    name: _t("Xóa lịch sử chat"),
    open({ store, thread }) {
        store.env.services.dialog.add(ConfirmationDialog, {
            title: _t("Xóa lịch sử chat?"),
            body: _t(
                "Toàn bộ tin nhắn và tệp đính kèm trong cuộc trò chuyện này sẽ bị xóa vĩnh viễn. Bạn vẫn có thể tiếp tục chat với SmartSolar AI."
            ),
            confirmLabel: _t("Xóa lịch sử"),
            confirmClass: "btn-danger",
            confirm: async () => {
                const result = await store.env.services.orm.call(
                    "discuss.channel",
                    "action_clear_smartsolar_ai_history",
                    [[thread.id]]
                );
                store.env.services.notification.add(
                    _t("Đã xóa %s tin nhắn.", result.deleted_count),
                    { type: "success" }
                );
                // Xóa cả các record message đang được OWL giữ trong bộ nhớ.
                browser.location.reload();
            },
        });
    },
    sequence: 50,
    sequenceGroup: 40,
});
