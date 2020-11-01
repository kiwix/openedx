# 1.0.1.dev0

- fixed recursive paths and URLs in html_processor.py
- fixed usage on older browsers (without ES6 support)
- added multithreading support
- fixed missing videos due to youtube_dl error
- added support for convertion in high quality
- use dynamic versions for opensans font
- fixed mobile navigation menu
- fixed video encoding in low quality in WebM
- added support for internationalization
- Fail properly on too many failed downloads or critical failures

# 1.0.0

- new structure for the project
- using fixed version of dependencies
- wiki and forums now optional
- using zimscraperlib for downloading and optimizing videos
- refactored Dockerfile
- added s3 based optimization cache
- removed javascript dependencies from repository
- added support for webm on systems without native support
- fixed small favicons
- using pylibzim for creating ZIMs
- fixed internal links
- using params instead of instance configs
- using course layout from instance
- added videojs based audio player to solve compatibility issues
- fixed problem answer fetching
