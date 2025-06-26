# -*- coding: utf-8 -*-
import logging
import os
import unittest

from lxml import etree

from axml.parser_arsc import ARSCResTableConfig, ARSCParser

test_dir = os.path.dirname(os.path.abspath(__file__))

from operator import itemgetter



TEST_APP_NAME = "TestsAndroguardApplication"
TEST_ICONS = {
    120: "res/drawable-ldpi/icon.png",
    160: "res/drawable-mdpi/icon.png",
    240: "res/drawable-hdpi/icon.png",
    65536: "res/drawable-hdpi/icon.png",
}
TEST_CONFIGS = {
    "layout": [ARSCResTableConfig.default_config()],
    "string": [ARSCResTableConfig.default_config()],
    "drawable": [
        ARSCResTableConfig(sdkVersion=4, density=120),
        ARSCResTableConfig(sdkVersion=4, density=160),
        ARSCResTableConfig(sdkVersion=4, density=240),
    ],
}


class ARSCTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(test_dir, 'data/ARSC/TestActivity_resources.arsc'), "rb") as arsc_b:
            cls.arsc = ARSCParser(arsc_b.read())

    def testARSC(self):
        self.assertTrue(self.arsc)

    def testAppName(self):
        app_name = '@7F040001'
        res_id, package = self.arsc.parse_id(app_name)
        locale = None
        try:
            config = (
                ARSCResTableConfig(None, locale=locale)
                if locale
                else ARSCResTableConfig.default_config()
            )
            app_name = self.arsc.get_resolved_res_configs(res_id, config)[0][1]
        except Exception as e:
            logging.warning("Exception selecting app name: %s" % e)
        self.assertEqual(
            app_name,
            TEST_APP_NAME,
            "Couldn't deduce application/activity label",
        )

    def testAppIcon(self):
        for wanted_density, correct_path in TEST_ICONS.items():
            app_icon = '@7F020000'
            if app_icon.startswith("@"):
                app_icon_id = app_icon[1:]
                app_icon_id = app_icon_id.split(':')[-1]
                res_id = int(app_icon_id, 16)
                candidates = self.arsc.get_resolved_res_configs(res_id)

                app_icon = None
                current_dpi = -1

                try:
                    for config, file_name in candidates:
                        dpi = config.get_density()
                        if current_dpi < dpi <= wanted_density:
                            app_icon = file_name
                            current_dpi = dpi
                except Exception as e:
                    logging.warning("Exception selecting app icon: %s" % e)

            # return app_icon
            self.assertEqual(
                app_icon,
                correct_path,
                "Incorrect icon path for requested density",
            )

    def testStrings(self):
        p = self.arsc.get_packages_names()[0]
        l = "\x00\x00"

        e = etree.fromstring(self.arsc.get_string_resources(p, l))

        self.assertEqual(
            e.find("string[@name='hello']").text,
            'Hello World, TestActivity! kikoololmodif',
        )
        self.assertEqual(
            e.find("string[@name='app_name']").text,
            'TestsAndroguardApplication',
        )

    def testResourceNames(self):
        """
        Test if the resource name translation works
        """

        self.assertEqual(
            self.arsc.get_resource_xml_name(0x7F040001),
            "@tests.androguard:string/app_name",
        )
        self.assertEqual(
            self.arsc.get_resource_xml_name(0x7F020000),
            "@tests.androguard:drawable/icon",
        )

        self.assertEqual(
            self.arsc.get_resource_xml_name(0x7F040001, 'tests.androguard'),
            "@string/app_name",
        )
        self.assertEqual(
            self.arsc.get_resource_xml_name(0x7F020000, 'tests.androguard'),
            "@drawable/icon",
        )

        # Also test non existing resources
        self.assertIsNone(self.arsc.get_resource_xml_name(0xFFFFFFFF))
        self.assertEqual(self.arsc.get_id('sdf', 0x7F040001), (None, None, None))
        self.assertEqual(
            self.arsc.get_id('tests.androguard', 0xFFFFFFFF), (None, None, None)
        )

    def testDifferentStringLocales(self):
        """
        Test if the resolving of different string locales works
        """
        with open(os.path.join(test_dir, 'data/ARSC/a2dp.Vol_137_resources.arsc'), "rb") as arsc_b:
            arsc = ARSCParser(arsc_b.read())
            p = arsc.get_packages_names()[0]

            self.assertEqual(
                sorted(["\x00\x00", "da", "de", "el", "fr", "ja", "ru"]),
                sorted(arsc.get_locales(p)),
            )

            item = "SMSDelayText"
            strings = {
                "\x00\x00": "Delay for reading text message",
                "da": "Forsinkelse for læsning af tekst besked",
                "de": "Verzögerung vor dem Lesen einer SMS",
                "el": "Χρονοκαθυστέρηση ανάγνωσης μηνυμάτων SMS",
                "fr": "Délai pour lire un SMS",
                "ja": "テキストメッセージ読み上げの遅延",
                "ru": "Задержка зачитывания SMS",
            }
            for k, v in strings.items():
                e = etree.fromstring(arsc.get_string_resources(p, k))
                self.assertEqual(e.find("string[@name='{}']".format(item)).text, v)

    def testTypeConfigs(self):
        configs = self.arsc.get_type_configs(None)

        for res_type, test_configs in list(TEST_CONFIGS.items()):
            config_set = set(test_configs)
            self.assertIn(
                res_type, configs, "resource type %s was not found" % res_type
            )
            for config in configs[res_type]:
                print(config.get_config_name_friendly())
                self.assertIn(
                    config, config_set, "config %r was not expected" % config
                )
                config_set.remove(config)

            self.assertEqual(
                len(config_set), 0, "configs were not found: %s" % config_set
            )

        unexpected_types = set(TEST_CONFIGS.keys()) - set(configs.keys())
        self.assertEqual(
            len(unexpected_types),
            0,
            "received unexpected resource types: %s" % unexpected_types,
        )

    def testFallback(self):
        with open(os.path.join(test_dir, 'data/ARSC/com.teleca.jamendo_35_resources.arsc'), "rb") as arsc_b:
            arsc = ARSCParser(arsc_b.read())

        res_id = 2131296258

        # Default Mode, no config
        self.assertEqual(len(arsc.get_res_configs(res_id)), 2)
        # With default config, but fallback
        self.assertEqual(
            len(
                arsc.get_res_configs(
                    res_id, ARSCResTableConfig.default_config()
                )
            ),
            1,
        )
        # With default config but no fallback
        self.assertEqual(
            len(
                arsc.get_res_configs(
                    res_id,
                    ARSCResTableConfig.default_config(),
                    fallback=False,
                )
            ),
            0,
        )

        # Also test on resolver:
        self.assertListEqual(
            list(
                map(itemgetter(1), arsc.get_resolved_res_configs(res_id))
            ),
            ["Jamendo", "Jamendo"],
        )
        self.assertListEqual(
            list(
                map(
                    itemgetter(1),
                    arsc.get_resolved_res_configs(
                        res_id, ARSCResTableConfig.default_config()
                    ),
                )
            ),
            ["Jamendo"],
        )

    def testIDParsing(self):
        parser = ARSCParser.parse_id

        self.assertEqual(parser('@DEADBEEF'), (0xDEADBEEF, None))
        self.assertEqual(parser('@android:DEADBEEF'), (0xDEADBEEF, 'android'))
        self.assertEqual(parser('@foobar:01020304'), (0x01020304, 'foobar'))

        with self.assertRaises(ValueError):
            parser('@whatisthis')
        with self.assertRaises(ValueError):
            parser('#almost')
        with self.assertRaises(ValueError):
            parser('@android:NONOTHEX')
        with self.assertRaises(ValueError):
            parser('@android:00')


if __name__ == '__main__':
    unittest.main()
