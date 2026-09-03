"""Unit tests for Pinterest Bulk Formatter copy-and-paste parsing and conversion."""

import io
import unittest
import pandas as pd
from fastapi.testclient import TestClient

from src.services.formatter import (
    clean_url,
    is_image_url,
    parse_pasted_data,
    inspect_master_csv,
    format_master_csv,
    clean_description,
    clean_title,
)
from src.app import app

SAMPLE_VERTICAL_PASTE = """main_keyword
search_volume
additional_keywords
related_interests
summary
board
blogpost_title
blogpost_url
keyword
pin_title
pin_description
image_prompt_index
image_prompt_text
week
Article link
Media Link
bottle crafts
226,299
diy bottle crafts, bottle craft ideas, diy bottle crafts ideas, bottle diy crafts, crafts with bottles, craft bottle ideas, bottle art and craft, art on bottles craft ideas, bottle craft diy, craft on bottle
jar crafts,jar art,waste material craft ideas,plastic bottle art,mini bottle painting,crafts with glass bottles,glass bottles art,recycling ideas,plastic bottles crafts,bottle art ideas,plastic bottle crafts,glass bottle crafts,diy bottle crafts,water bottle crafts,bottle craft ideas,medicine bottle crafts,mini glass bottle crafts,small glass bottle crafts,coke bottle crafts,patron bottle crafts
quick crafts
29 Bottle Crafts That Will Blow Your Mind (Easy DIY Projects)
/bottle-crafts
diy bottle crafts
29 Bottle Crafts: DIY Bottle Crafts for Home Decor & Gifts
Unleash your creativity with 29 bottle crafts featuring simple diy bottle crafts. From bottle art ideas to recycling ideas, these projects include plastic bottles crafts and glass bottle crafts. Incorporate jar art and mini glass bottle crafts for unique decor. Perfect for diy bottle crafts enthusiasts looking for waste material craft ideas. Turn ordinary bottles into personalized gifts with these bottle craft ideas!
2
2 - Create a viral Pinterest pin that is a 2 to 6 collage of photos. The central focus must be groovy, retro, bold text that says "29 Bottle Crafts: DIY Bottle Crafts for Home Decor & Gifts". This text must be capitalized, colored white, and have a distinct pink outline.
Week 2
[https://postagemaster.com/bottle-crafts](https://postagemaster.com/bottle-crafts)
[https://i.ibb.co/JWy0HC7M/20260902-2-Create-a-viral-Pinterest-pin-that-is.jpg](https://i.ibb.co/JWy0HC7M/20260902-2-Create-a-viral-Pinterest-pin-that-is.jpg)
art and craft
163,450
craft ideas, diy and crafts, diy arts and crafts, art and craft ideas, crafts ideas, the craft, diy crafts to do, crafts to do, arts and crafts ideas, diy crafts ideas
easy paper crafts diy,painting ideas,quick crafts,cute craft ideas,creative arts and crafts,arts and crafts projects,arts and crafts easy,diy arts and crafts,craft ideas for kids,crafts for adults,arts and crafts for kids,art and craft ideas,summer arts and crafts,arts and crafts aesthetic,arts and crafts movement,spring arts and crafts,arts and crafts for preschoolers,halloween arts and crafts,easy arts and crafts,art and craft ideas creativity
quick crafts
29 Creative Art and Craft Ideas to Inspire Your Next Project
/art-and-craft-ideas
craft ideas
29 Craft Ideas to Spark Your Imagination ✨
Discover 29 craft ideas that spark joy and creativity. From easy paper creations to diy arts and crafts, these projects suit all ages. Use simple supplies to make beautiful gifts and decor. Try painting, sewing, or quick projects for instant fun. Elevate your art and craft skills with these cute ideas. Perfect for weekends or group activities. 🎨✨
13
13 - Create a viral Pinterest pin that is a 2 to 6 collage of photos. The central focus must be groovy, retro, bold text that says "29 Craft Ideas to Spark Your Imagination ✨". This text must be capitalized, colored white, and have a distinct pink outline.
Week 2
[https://postagemaster.com/art-and-craft-ideas](https://postagemaster.com/art-and-craft-ideas)
[https://i.ibb.co/shYsmbf/20260902-13-Create-a-viral-Pinterest-pin-that-i.jpg](https://i.ibb.co/shYsmbf/20260902-13-Create-a-viral-Pinterest-pin-that-i.jpg)
wood crafts
148,638
wood craft, wood crafts diy, wood craft ideas, wood crafts ideas, wood craft projects, handmade wood crafts, craft wood, crafts with wood, diy wood craft ideas, diy crafts wood
small wooden projects,easy wood crafts,scrap wood projects to sell,woodworking projects diy,cute wood projects,wooden diy crafts,scrap wood art,wooden crafts ideas,little wood projects,wood christmas crafts,christmas wood crafts,scrap wood crafts,wood craft patterns,wood crafts diy,4th of july wood crafts,halloween wood crafts,laser cut wood crafts,barn wood crafts,4x4 wood crafts,snowman wood crafts
quick crafts
29 Brilliant Wood Crafts That Will Transform Your Home Decor
/wood-crafts
wood craft
29 Wood Craft Projects: Must-Try Wood Crafts for DIY Lovers 🪚
Find 29 stunning wood craft projects featuring easy wood crafts and cute wood projects. These small wooden DIYs include scrap wood ideas perfect for selling. Start with woodworking projects today! 🛠️
24
24 - Create a viral Pinterest pin that is a 2 to 6 collage of photos. The central focus must be groovy, retro, bold text that says "29 Wood Craft Projects: Must-Try Wood Crafts for DIY Lovers 🪚". This text must be capitalized, colored white, and have a distinct pink outline.
Week 2
[https://postagemaster.com/wood-crafts](https://postagemaster.com/wood-crafts)
[https://i.ibb.co/DDQp9MWm/20260902-24-Create-a-viral-Pinterest-pin-that-i.jpg](https://i.ibb.co/DDQp9MWm/20260902-24-Create-a-viral-Pinterest-pin-that-i.jpg)
ice cream stick craft
145,665
craft with ice cream sticks, ice cream sticks craft ideas art, diy ice cream stick craft ideas, stick ice cream craft, crafts with ice cream sticks, ice cream stick craft ideas, ice cream stick crafts, craft ideas with ice cream sticks, craft with ice cream sticks ideas, craft ice cream sticks
paper crafts,ice cream sticks craft ideas wall hangings,pop stick craft,popstick craft diy,popsicle stick crafts for kids,icecreamsticks crafts,ice sticks craft ideas,ice cream stick pen holder,lolly stick craft,ice cream stick bookmark,ice cream stick craft decoration,ice cream sticks craft ideas art,ice cream stick crafts for kids,ice cream sticks craft ideas for kids,ice cream stick craft easy,ice cream stick craft aesthetic,simple ice cream stick craft,ice cream stick craft christmas,craft with ice cream sticks ideas,art and craft with ice cream sticks
quick crafts
29 Creative Ice Cream Stick Craft Ideas for Endless DIY Fun
/ice-cream-stick-craft
craft with ice cream sticks
29 Ice Cream Stick Craft: Easy Crafts with Ice Cream Sticks
Explore 29 fun ways to craft with ice cream sticks, from simple bookmarks to intricate wall art. 🌟 This roundup includes ice cream stick craft ideas for kids and adults alike. Try popsicle stick crafts for kids or create a beautiful ice cream stick craft decoration. We love using lolly stick craft techniques and ice cream stick bookmark designs. Grab your supplies and dive into these craft with ice cream sticks ideas!
35
35 - Create a viral Pinterest pin that is a 2 to 6 collage of photos. The central focus must be groovy, retro, bold text that says "29 Ice Cream Stick Craft: Easy Crafts with Ice Cream Sticks". This text must be capitalized, colored white, and have a distinct pink outline.
Week 2
[https://postagemaster.com/ice-cream-stick-craft](https://postagemaster.com/ice-cream-stick-craft)
[https://i.ibb.co/1fV8NnMH/20260902-35-Create-a-viral-Pinterest-pin-that-i.jpg](https://i.ibb.co/1fV8NnMH/20260902-35-Create-a-viral-Pinterest-pin-that-i.jpg)"""


class TestFormatter(unittest.TestCase):
    def test_clean_url(self):
        self.assertEqual(
            clean_url("[https://postagemaster.com/bottle-crafts](https://postagemaster.com/bottle-crafts)"),
            "https://postagemaster.com/bottle-crafts",
        )
        self.assertEqual(
            clean_url("[Click Here](https://i.ibb.co/JWy0HC7M/pin.jpg)"),
            "https://i.ibb.co/JWy0HC7M/pin.jpg",
        )
        self.assertEqual(
            clean_url("https://normal-url.com/path"),
            "https://normal-url.com/path",
        )
        self.assertEqual(clean_url(""), "")

    def test_parse_sample_vertical_paste(self):
        df = parse_pasted_data(SAMPLE_VERTICAL_PASTE)
        self.assertEqual(len(df), 4)
        self.assertIn("pin_title", df.columns)
        self.assertIn("Media Link", df.columns)
        self.assertIn("board", df.columns)
        self.assertIn("pin_description", df.columns)
        self.assertIn("Article link", df.columns)

        # Check first row
        row0 = df.iloc[0]
        self.assertEqual(row0["board"], "quick crafts")
        self.assertEqual(row0["pin_title"], "29 Bottle Crafts: DIY Bottle Crafts for Home Decor & Gifts")
        self.assertEqual(row0["Article link"], "https://postagemaster.com/bottle-crafts")
        self.assertEqual(
            row0["Media Link"],
            "https://i.ibb.co/JWy0HC7M/20260902-2-Create-a-viral-Pinterest-pin-that-is.jpg",
        )
        self.assertEqual(row0["week"], "Week 2")

    def test_parse_tsv_paste(self):
        tsv_data = (
            "Title	Media URL	Pinterest Board	Description	Link\n"
            "Pin One	https://i.ibb.co/abc/1.jpg	Crafts	Cool craft description.	https://site.com/1\n"
            "Pin Two	https://i.ibb.co/xyz/2.jpg	DIY	Awesome DIY description.	https://site.com/2"
        )
        df = parse_pasted_data(tsv_data)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["Title"], "Pin One")
        self.assertEqual(df.iloc[1]["Media URL"], "https://i.ibb.co/xyz/2.jpg")

    def test_parse_markdown_table_paste(self):
        md_table = (
            "| Title | Media URL | Pinterest Board | Description | Link |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| MD Pin | https://i.ibb.co/md/img.jpg | MD Board | MD Description | https://site.com/md |"
        )
        df = parse_pasted_data(md_table)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["Title"], "MD Pin")
        self.assertEqual(df.iloc[0]["Pinterest Board"], "MD Board")

    def test_inspect_and_format_pipeline(self):
        info = inspect_master_csv(SAMPLE_VERTICAL_PASTE)
        self.assertTrue(info["valid"])
        self.assertEqual(info["total_rows"], 4)
        self.assertEqual(info["mapped_columns"]["title"], "pin_title")
        self.assertEqual(info["mapped_columns"]["media_url"], "Media Link")
        self.assertEqual(len(info["detected_weeks"]), 1)
        self.assertEqual(info["detected_weeks"][0]["week_num"], 2)

        out_df, qa_report = format_master_csv(
            SAMPLE_VERTICAL_PASTE,
            target_template="pinterest",
            schedule_publish_dates=True,
            publish_start_date="2026-09-04",
        )
        self.assertEqual(len(out_df), 4)
        self.assertIn("Title", out_df.columns)
        self.assertIn("Publish Date", out_df.columns)
        self.assertTrue(all(len(t) <= 100 for t in out_df["Title"]))
        self.assertTrue(all(len(d) <= 500 for d in out_df["Description"]))
        self.assertEqual(qa_report["total_output_pins"], 4)

    def test_api_inspect_and_convert_endpoints(self):
        client = TestClient(app)

        # 1. Inspect endpoint with raw_text
        res = client.post("/api/formatter/inspect", data={"raw_text": SAMPLE_VERTICAL_PASTE})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["valid"])
        self.assertEqual(data["total_rows"], 4)

        # 2. Convert endpoint with raw_text
        res_conv = client.post(
            "/api/formatter/convert",
            data={
                "raw_text": SAMPLE_VERTICAL_PASTE,
                "target_template": "pinterest",
                "schedule_publish_dates": "true",
                "publish_start_date": "2026-09-04",
            },
        )
        self.assertEqual(res_conv.status_code, 200)
        self.assertIn("attachment; filename=", res_conv.headers.get("content-disposition", ""))
        content = res_conv.content.decode("utf-8-sig")
        self.assertIn("Title,Media URL,Pinterest Board", content)
        self.assertIn("29 Bottle Crafts: DIY Bottle Crafts for Home Decor & Gifts", content)


if __name__ == "__main__":
    unittest.main()
