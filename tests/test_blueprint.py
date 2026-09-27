import unittest

try:
    import yaml
    import jinja2
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


if HAS_DEPS:
    class Input(object):
        def __init__(self, val):
            self.val = val

        def __repr__(self):
            return f"!input {self.val}"

    yaml.SafeLoader.add_constructor(
        "!input", lambda loader, node: Input(loader.construct_scalar(node))
    )


from pathlib import Path

BLUEPRINT_PATH = Path(__file__).resolve().parent.parent / "blueprints" / "automation" / "waste_collection_reminder.yaml"


class TestBlueprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_DEPS:
            raise unittest.SkipTest("yaml and jinja2 are required for blueprint tests")
        with open(BLUEPRINT_PATH, "r", encoding="utf-8") as f:
            cls.blueprint_content = f.read()
        cls.data = yaml.safe_load(cls.blueprint_content)
        cls.jinja_env = jinja2.Environment()

    def test_blueprint_metadata(self):
        self.assertIn("blueprint", self.data)
        bp = self.data["blueprint"]
        self.assertEqual(bp["domain"], "automation")
        self.assertIn("name", bp)
        self.assertIn("description", bp)
        self.assertIn("input", bp)

    def test_blueprint_inputs(self):
        inputs = self.data["blueprint"]["input"]
        expected_inputs = [
            "calendar_entity",
            "notify_service",
            "notify_device",
            "first_reminder_offset",
            "second_reminder_offset",
            "enable_second_reminder",
            "custom_action",
        ]
        for key in expected_inputs:
            self.assertIn(key, inputs)

        # Check calendar entity selector
        self.assertEqual(
            inputs["calendar_entity"]["selector"]["entity"]["filter"]["domain"],
            "calendar",
        )

        # Check default offsets
        self.assertEqual(inputs["first_reminder_offset"]["default"], "-04:00:00")
        self.assertEqual(inputs["second_reminder_offset"]["default"], "-02:00:00")
        self.assertTrue(inputs["enable_second_reminder"]["default"])

    def test_triggers_and_modes(self):
        self.assertEqual(self.data.get("mode"), "parallel")
        triggers = self.data.get("triggers", [])
        self.assertEqual(len(triggers), 2)
        self.assertEqual(triggers[0]["id"], "night_before")
        self.assertEqual(triggers[1]["id"], "reminder")

    def test_target_service_resolution(self):
        variables = self.data.get("variables", {})
        target_template_str = variables.get("target_service")
        self.assertIsNotNone(target_template_str)

        template = self.jinja_env.from_string(target_template_str)

        # 1. Custom notify service provided
        res1 = template.render(
            notify_service="notify.jordan_and_kate_phones",
            notify_device="",
        ).strip()
        self.assertEqual(res1, "notify.jordan_and_kate_phones")

        # 2. Device provided with slugify mock
        jinja_env_slug = jinja2.Environment()
        jinja_env_slug.filters["slugify"] = lambda s: s.lower().replace(" ", "_")
        template_slug = jinja_env_slug.from_string(target_template_str)

        # When device_attr returns a device name
        def fake_device_attr(device_id, attr):
            return "Pixel 7 Pro"

        res2 = template_slug.render(
            notify_service="",
            notify_device="abc12345",
            device_attr=fake_device_attr,
        ).strip()
        self.assertEqual(res2, "notify.mobile_app_pixel_7_pro")

        # 3. Fallback when neither provided
        res3 = template.render(notify_service="", notify_device="").strip()
        self.assertEqual(res3, "notify.notify")

    def test_category_matching(self):
        test_cases = [
            ("Organic Waste - Sector A", "🌿 Compost Tomorrow", "mdi:sprout", "#4caf50"),
            ("Recyclables - Sector A", "♻️ Recycling Tomorrow", "mdi:recycle", "#1e88e5"),
            ("Household Waste - Sector A", "🗑️ Garbage Tomorrow", "mdi:delete-empty", "#757575"),
            ("Bulky Items - Sector A", "📦 Bulky Items Tomorrow", "mdi:couch", "#8d6e63"),
            ("Leaf Collection - Sector A", "🍂 Leaf Collection Tomorrow", "mdi:leaf-maple", "#e65100"),
            ("Christmas Tree Collection - Sector A", "🎄 Christmas Tree Collection Tomorrow", "mdi:pine-tree", "#2e7d32"),
            ("Mattress/Box-Spring Collection - Sector A", "📦 Bulky Items Tomorrow", "mdi:couch", "#8d6e63"),
            ("Ecocentre Collection - Sector A", "♻️ Ecocentre Drop-off Tomorrow", "mdi:recycle-variant", "#0288d1"),
            ("Random Unknown Collection", "🗑️ Garbage Tomorrow", "mdi:delete-empty", "#757575"),
        ]

        title_template_str = self.data["actions"][0]["choose"][0]["sequence"][0]["data"]["title"]
        icon_template_str = self.data["actions"][0]["choose"][0]["sequence"][0]["data"]["data"]["notification_icon"]
        color_template_str = self.data["actions"][0]["choose"][0]["sequence"][0]["data"]["data"]["color"]

        title_tpl = self.jinja_env.from_string(title_template_str)
        icon_tpl = self.jinja_env.from_string(icon_template_str)
        color_tpl = self.jinja_env.from_string(color_template_str)

        for summary, expected_title, expected_icon, expected_color in test_cases:
            rendered_title = title_tpl.render(summary=summary.lower()).strip()
            rendered_icon = icon_tpl.render(summary=summary.lower()).strip()
            rendered_color = color_tpl.render(summary=summary.lower()).strip()

            self.assertEqual(rendered_title, expected_title, f"Failed title for {summary}")
            self.assertEqual(rendered_icon, expected_icon, f"Failed icon for {summary}")
            self.assertEqual(rendered_color, expected_color, f"Failed color for {summary}")


if __name__ == "__main__":
    unittest.main()
