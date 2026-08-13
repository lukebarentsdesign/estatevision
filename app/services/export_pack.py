"""Export pack generator (spec §6.4).

Bundles property assets, videos, standalone HTML microsite, 7-day social media
calendar, and outputs a single downloadable ZIP file.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional


def generate_microsite_html(
    address: str,
    postcode: str,
    price_guide: str,
    garden_orientation: str,
    agency_name: str,
    primary_color: str = "#1E293B",
    secondary_color: str = "#0F172A",
    logo_url: str = "",
    staff_name: str = "",
    staff_headshot: str = "",
    script_summary: str = "",
    location_data: Optional[Dict[str, Any]] = None,
    video_16x9_url: str = "",
    video_9x16_urls: Optional[list[str]] = None,
    photos: Optional[list[str]] = None,
) -> str:
    """Renders a responsive, standalone HTML property landing page with inline Tailwind CSS."""
    location_data = location_data or {}
    amenities = location_data.get("amenities", [])
    daylight = (location_data.get("daylight") or {}).get("statement", "")
    photos = photos or []
    video_9x16_urls = video_9x16_urls or []

    amenities_html = "".join(
        f'<span class="bg-slate-800 text-slate-300 text-xs px-3 py-1.5 rounded-full border border-slate-700">{a.get("name")} ({a.get("type","")})</span>'
        for a in amenities[:6]
    ) or '<span class="text-slate-400 text-sm">Cafes, stations, and parks nearby.</span>'

    gallery_html = "".join(
        f'<div class="aspect-video bg-slate-800 rounded-xl overflow-hidden shadow-lg"><img src="{p}" class="w-full h-full object-cover" alt="Property photo" /></div>'
        for p in photos
    ) or '<p class="text-slate-400">Photo gallery available in main pack.</p>'

    html = f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{address} — Property Microsite</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {{
      --primary: {primary_color};
      --secondary: {secondary_color};
    }}
  </style>
</head>
<body class="bg-slate-950 text-slate-100 font-sans antialiased selection:bg-indigo-500 selection:text-white">
  <!-- Header -->
  <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
    <div class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        {f'<img src="{logo_url}" class="h-8 object-contain" alt="Agency Logo" />' if logo_url else ''}
        <span class="font-bold text-lg text-slate-100">{agency_name or 'Estate Agency'}</span>
      </div>
      <a href="#contact" class="bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-4 py-2 rounded-lg text-sm transition">Book Viewing</a>
    </div>
  </header>

  <!-- Hero Section -->
  <section class="max-w-6xl mx-auto px-6 py-12">
    <div class="inline-block bg-indigo-950/80 border border-indigo-500/30 text-indigo-300 text-xs font-semibold px-3 py-1 rounded-full mb-4">
      {price_guide or 'Guide Price on Application'}
    </div>
    <h1 class="text-4xl md:text-5xl font-extrabold text-white tracking-tight mb-3">{address}</h1>
    <p class="text-slate-400 text-lg mb-8">{postcode} • Garden Orientation: {garden_orientation}</p>

    <!-- Main Video Player -->
    {f'''
    <div class="aspect-video bg-black rounded-2xl overflow-hidden shadow-2xl border border-slate-800 mb-12">
      <video controls class="w-full h-full object-cover">
        <source src="{video_16x9_url}" type="video/mp4">
        Your browser does not support the video tag.
      </video>
    </div>
    ''' if video_16x9_url else ''}

    <!-- Overview & Script -->
    <div class="grid grid-cols-1 gap-8 mb-16">
      <div class="bg-slate-900/60 border border-slate-800 p-8 rounded-2xl">
        <h2 class="text-2xl font-bold text-white mb-4">Property Highlights</h2>
        <p class="text-slate-300 leading-relaxed whitespace-pre-line">{script_summary or 'Stunning property with premium features throughout.'}</p>
        {f'<p class="mt-4 text-sm text-indigo-400 font-medium">☀️ {daylight}</p>' if daylight else ''}
      </div>
    </div>

    <!-- Amenities & Gallery -->
    <div class="mb-16">
      <h3 class="text-xl font-bold text-white mb-4">Local Amenities</h3>
      <div class="flex flex-wrap gap-2 mb-8">
        {amenities_html}
      </div>

      <h3 class="text-xl font-bold text-white mb-6">Gallery</h3>
      <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-6">
        {gallery_html}
      </div>
    </div>

    <!-- Contact Agent Box -->
    <div id="contact" class="bg-gradient-to-r from-slate-900 to-indigo-950 border border-indigo-500/20 p-8 rounded-2xl flex flex-col md:flex-row items-center justify-between gap-6">
      <div class="flex items-center space-x-4">
        {f'<img src="{staff_headshot}" class="w-16 h-16 rounded-full object-cover border-2 border-indigo-500" alt="{staff_name}" />' if staff_headshot else '<div class="w-16 h-16 rounded-full bg-indigo-600 flex items-center justify-center font-bold text-xl text-white">AG</div>'}
        <div>
          <h3 class="text-xl font-bold text-white">{staff_name or 'Property Specialist'}</h3>
          <p class="text-slate-400 text-sm">{agency_name or 'Estate Agency Team'}</p>
        </div>
      </div>
      <button onclick="alert('Viewing request registered for {address}')" class="bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-6 py-3 rounded-xl transition shadow-lg">
        Request Private Tour
      </button>
    </div>
  </section>

  <footer class="border-t border-slate-800 py-8 text-center text-slate-500 text-sm">
    &copy; {agency_name or 'Property Content Studio'}. All rights reserved.
  </footer>
</body>
</html>
"""
    return html


def generate_social_calendar_md(address: str, postcode: str, price_guide: str, script_json: Optional[Dict[str, Any]] = None) -> str:
    """Generates a 7-day social media promotional marketing plan."""
    script_json = script_json or {}
    walkthrough = script_json.get("walkthrough_script", "Exclusive walkthrough tour of this gorgeous home.")
    shorts = script_json.get("social_shorts", [
        "Check out this stunning property listing!",
        "Key features you will love about this home."
    ])

    short1 = shorts[0] if len(shorts) > 0 else walkthrough[:120]
    short2 = shorts[1] if len(shorts) > 1 else walkthrough[120:240]

    content = f"""# 7-Day Social Media Launch Plan
**Property:** {address} ({postcode})
**Guide Price:** {price_guide}

---

### 📅 Day 1 — Coming Soon / Teaser
- **Platform:** Instagram Reels / TikTok / YouTube Shorts (9:16)
- **Asset:** 15s Teaser Clip
- **Caption:**
  "Sneak peek! 🎬 Coming to market soon in {postcode}. A incredible property with fantastic features. DM us for early access! 🏡✨ #PropertyTeaser #{postcode.replace(' ', '')} #RealEstate"

---

### 📅 Day 2 — Official Market Launch & Full Video
- **Platform:** Facebook / LinkedIn / YouTube (16:9)
- **Asset:** Full Master Walkthrough Video
- **Caption:**
  "JUST LAUNCHED! 🚀 Welcome to {address}.\n\n{short1}\n\nContact our team today to arrange your private viewing."

---

### 📅 Day 3 — Feature Highlight Reel
- **Platform:** Instagram / Facebook Stories
- **Asset:** Social Short #1
- **Caption:**
  "Highlighting the best spaces at {address}! What's your favourite room? Let us know in the comments below! 👇"

---

### 📅 Day 4 — Local Area & Lifestyle Spotlight
- **Platform:** Instagram Post / Carousel
- **Asset:** Photos & Connectivity Infographic
- **Caption:**
  "Location matters! 📍 Great transport links and local amenities right on your doorstep in {postcode}. Slide to explore the neighbourhood!"

---

### 📅 Day 5 — High-Impact Motion Reel
- **Platform:** TikTok / Instagram Reels
- **Asset:** Social Short #2
- **Caption:**
  "Turn up the volume 🔊 Take a high-speed motion tour through {address}! Link in bio to view full details."

---

### 📅 Day 6 — Open House / Viewing Reminder
- **Platform:** Facebook & Instagram Stories
- **Asset:** Key Photo + Call to Action Overlay
- **Caption:**
  "Weekend viewings filling up fast for {address}. Send us a message now to secure your slot!"

---

### 📅 Day 7 — Weekly Property Wrap-Up
- **Platform:** Newsletter / LinkedIn
- **Asset:** Standalone Microsite Link + Video Recap
- **Caption:**
  "In case you missed it this week — check out our featured property {address}. Tap the link to view the complete digital brochure & interactive tour."
"""
    return content


def build_export_zip(
    job_id: int,
    address: str,
    postcode: str,
    price_guide: str,
    garden_orientation: str,
    agency_name: str,
    primary_color: str,
    secondary_color: str,
    logo_url: str,
    staff_name: str,
    staff_headshot: str,
    script_json: Optional[Dict[str, Any]],
    location_data: Optional[Dict[str, Any]],
    work_dir: Path,
) -> bytes:
    """Builds an in-memory ZIP package containing all export deliverables."""
    zip_buffer = io.BytesIO()

    script_summary = ""
    if script_json and "walkthrough_script" in script_json:
        script_summary = script_json["walkthrough_script"]

    # 1. Generate Microsite HTML
    microsite_html = generate_microsite_html(
        address=address,
        postcode=postcode,
        price_guide=price_guide,
        garden_orientation=garden_orientation,
        agency_name=agency_name,
        primary_color=primary_color,
        secondary_color=secondary_color,
        logo_url=logo_url,
        staff_name=staff_name,
        staff_headshot=staff_headshot,
        script_summary=script_summary,
        location_data=location_data,
        video_16x9_url="master_16x9.mp4",
        video_9x16_urls=["reel_9x16.mp4"],
        photos=["photo_1.jpg", "photo_2.jpg"],
    )

    # 2. Generate Social Media Calendar
    calendar_md = generate_social_calendar_md(
        address=address,
        postcode=postcode,
        price_guide=price_guide,
        script_json=script_json,
    )

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Save HTML & Calendar
        zf.writestr("microsite/index.html", microsite_html)
        zf.writestr("social_calendar_7day.md", calendar_md)

        # Save metadata summary
        meta_info = {
            "job_id": job_id,
            "address": address,
            "postcode": postcode,
            "agency_name": agency_name,
        }
        zf.writestr("export_metadata.json", json.dumps(meta_info, indent=2))

        # Include output videos if present in work_dir
        if work_dir.exists():
            for video_file in work_dir.glob("*.mp4"):
                zf.write(video_file, arcname=f"videos/{video_file.name}")

    zip_buffer.seek(0)
    return zip_buffer.getvalue()
