from __future__ import annotations

import unittest

from config import CATEGORIES
from views.ticket_controls import PrioritySelectView, TicketControlsView
from views.ticket_feedback import FeedbackView
from views.ticket_panel import TicketPanelView


class ViewTests(unittest.TestCase):
    def test_panel_has_exact_five_requested_categories(self) -> None:
        expected = {"general", "reports", "applications", "partnerships", "other"}
        self.assertEqual(set(CATEGORIES), expected)
        panel = TicketPanelView(bot=None)
        selector = panel.children[0]
        self.assertEqual({option.value for option in selector.options}, expected)
        self.assertEqual(len(selector.options), 5)

    def test_ticket_controls_are_complete_and_fit_discord_rows(self) -> None:
        controls = TicketControlsView()
        custom_ids = {item.custom_id for item in controls.children}
        actions = {
            "claim", "unclaim", "close", "reopen", "delete", "add", "remove",
            "rename", "priority", "assign", "transfer", "lock", "unlock", "transcript",
        }
        self.assertEqual(custom_ids, {f"z9:ticket:{action}" for action in actions})
        self.assertTrue(all(item.row in {0, 1, 2} for item in controls.children))
        self.assertLessEqual(max(sum(item.row == row for item in controls.children) for row in range(3)), 5)

    def test_priority_and_feedback_views_are_persistent_and_complete(self) -> None:
        priority = PrioritySelectView(bot=None, ticket_id=42)
        self.assertEqual(
            {option.value for option in priority.children[0].options},
            {"LOW", "NORMAL", "HIGH", "URGENT"},
        )
        feedback = FeedbackView(ticket_id=42, guild_id=1, owner_id=2)
        self.assertIsNone(feedback.timeout)
        self.assertEqual(
            {item.custom_id for item in feedback.children},
            {f"z9:feedback:42:{rating}" for rating in range(1, 6)},
        )
