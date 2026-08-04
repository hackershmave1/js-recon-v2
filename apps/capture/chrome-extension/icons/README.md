# Extension Icons

Place your icon files here:

- icon16.png (16x16)
- icon48.png (48x48)  
- icon128.png (128x128)

You can use these placeholder icons or create your own:

## Using ImageMagick to create simple icons:

```bash
# Create a simple blue icon
convert -size 128x128 xc:#2196F3 \
  -gravity center \
  -pointsize 60 -fill white \
  -annotate +0+0 "JS" \
  icon128.png

# Resize for other sizes
convert icon128.png -resize 48x48 icon48.png
convert icon128.png -resize 16x16 icon16.png
```

## Or use an online icon generator:
- https://icon.kitchen/
- https://www.favicon-generator.org/
