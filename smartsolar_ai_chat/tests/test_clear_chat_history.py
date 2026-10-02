# -*- coding: utf-8 -*-

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase


class TestClearChatHistory(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bot_user = cls.env.ref('smartsolar_ai_chat.user_smartsolar_ai')
        cls.user = cls.env['res.users'].create({
            'name': 'SmartSolar history test user',
            'login': 'smartsolar_history_test_user',
        })
        cls.channel = cls.env['discuss.channel'].with_user(cls.user).create({
            'name': 'SmartSolar history test',
            'channel_type': 'chat',
            'channel_member_ids': [
                (0, 0, {'partner_id': cls.user.partner_id.id}),
                (0, 0, {'partner_id': cls.bot_user.partner_id.id}),
            ],
        })

    def test_clear_history_keeps_channel(self):
        self.channel.message_post(body='Câu hỏi thử nghiệm')
        self.channel.message_post(
            author_id=self.bot_user.partner_id.id,
            body='Câu trả lời thử nghiệm',
        )

        result = self.channel.with_user(self.user).action_clear_smartsolar_ai_history()

        self.assertGreaterEqual(result['deleted_count'], 2)
        self.assertTrue(self.channel.exists())
        remaining = self.env['mail.message'].search_count([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', self.channel.id),
        ])
        self.assertEqual(remaining, 0)

    def test_non_member_cannot_clear_history(self):
        outsider = self.env['res.users'].create({
            'name': 'SmartSolar history outsider',
            'login': 'smartsolar_history_outsider',
        })
        with self.assertRaises(AccessError):
            self.channel.with_user(outsider).action_clear_smartsolar_ai_history()
