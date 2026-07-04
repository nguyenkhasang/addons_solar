# -*- coding: utf-8 -*-
"""Hook Discuss: kích hoạt SmartSolar AI khi user nhắn tin cho bot.

Theo đúng cơ chế OdooBot dùng (_message_post_after_hook trên discuss.channel),
nhưng nhắm vào partner bot riêng 'SmartSolar AI' thay vì OdooBot -> không đụng
onboarding logic của Odoo, không vỡ khi nâng cấp.
"""
from __future__ import annotations

import logging

from markupsafe import Markup

from odoo import models

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def _message_post_after_hook(self, message, msg_vals):
        result = super()._message_post_after_hook(message, msg_vals)
        try:
            self._smartsolar_ai_maybe_reply(message)
        except Exception as e:
            _logger.exception('SmartSolar AI reply failed: %s', e)
        return result

    def _smartsolar_ai_maybe_reply(self, message):
        """Nếu tin nhắn gửi trong channel có bot AI và không phải do bot gửi,
        chạy planner loop và đăng câu trả lời.

        Đọc từ record `message` (đáng tin hơn dict msg_vals). Log mỗi bước để
        chẩn đoán khi bot không phản hồi.
        """
        # Nguồn duy nhất: partner của bot = user.partner_id (tránh lệch partner).
        bot_user = self.env.ref(
            'smartsolar_ai_chat.user_smartsolar_ai', raise_if_not_found=False)
        if not bot_user:
            _logger.info('SmartSolar AI: không tìm thấy user bot (chưa cài data?)')
            return
        bot_partner = bot_user.partner_id

        # Bỏ qua tin do chính bot đăng (tránh vòng lặp vô tận).
        if message.author_id and message.author_id.id == bot_partner.id:
            return
        # Chỉ xử lý tin bình luận thật của người dùng.
        if message.message_type != 'comment':
            _logger.info('SmartSolar AI: bỏ qua message_type=%s', message.message_type)
            return
        # Chỉ trả lời trong channel mà bot là thành viên.
        member_partners = self.channel_member_ids.partner_id
        if bot_partner not in member_partners:
            _logger.info('SmartSolar AI: bot(partner=%s) không phải thành viên channel %s (members=%s)',
                         bot_partner.id, self.id, member_partners.ids)
            return

        question = self._smartsolar_html_to_text(message.body or '').strip()
        if not question:
            return

        # Nạp ngữ cảnh hội thoại (các tin trước) để AI "nhớ" câu hỏi/đáp trước đó.
        history = self._smartsolar_build_history(message, bot_partner)

        _logger.info('SmartSolar AI: nhận câu hỏi "%s" -> chạy planner (history=%d tin)',
                     question, len(history))
        answer = self.env['smartsolar.ai.agent'].chat(question, history=history)
        _logger.info('SmartSolar AI: trả lời (%d ký tự)', len(answer or ''))

        # Đăng câu trả lời dưới danh nghĩa bot.
        self.sudo().message_post(
            author_id=bot_partner.id,
            body=self._smartsolar_text_to_html(answer),
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    def _smartsolar_build_history(self, current_message, bot_partner):
        """Dựng danh sách ngữ cảnh hội thoại cho LLM từ các tin TRƯỚC trong kênh.

        Trả về list[{'role': 'user'|'assistant', 'content': str}] theo thứ tự thời
        gian tăng dần, KHÔNG gồm tin hiện tại (nó được thêm riêng ở planner).

        Vai trò: tin do bot đăng -> 'assistant'; tin của người dùng -> 'user'.
        Chỉ lấy tin 'comment' có nội dung; giới hạn theo config history_limit (0 = tắt).
        """
        Param = self.env['ir.config_parameter'].sudo()
        limit = int(Param.get_param('smartsolar_ai.history_limit', 6) or 0)
        if limit <= 0:
            return []

        # Lấy các tin bình luận trước tin hiện tại, mới nhất trước, rồi đảo lại.
        domain = [
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', self.id),
            ('message_type', '=', 'comment'),
            ('id', '<', current_message.id),
        ]
        recent = self.env['mail.message'].sudo().search(
            domain, order='id desc', limit=limit)

        history = []
        for msg in reversed(recent):
            content = self._smartsolar_html_to_text(msg.body or '').strip()
            if not content:
                continue
            is_bot = msg.author_id and msg.author_id.id == bot_partner.id
            history.append({
                'role': 'assistant' if is_bot else 'user',
                'content': content,
            })
        return history

    @staticmethod
    def _smartsolar_html_to_text(html):
        """Bóc thẻ HTML thô từ body tin nhắn thành text để đưa cho LLM."""
        import re
        text = re.sub(r'<br\s*/?>', '\n', html or '')
        text = re.sub(r'<[^>]+>', '', text)
        # Giải mã một số entity phổ biến.
        for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'),
                     ('&gt;', '>'), ('&#39;', "'"), ('&quot;', '"')):
            text = text.replace(a, b)
        return text

    @staticmethod
    def _smartsolar_text_to_html(text):
        """Chuyển câu trả lời text sang HTML an toàn (giữ xuống dòng)."""
        escaped = (text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return Markup(escaped.replace('\n', '<br/>'))
