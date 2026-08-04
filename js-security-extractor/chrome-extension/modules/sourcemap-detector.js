export class SourceMapDetector {
  detect(content, fileUrl) {
    const urlComment = this.extractFromComment(content);
    if (urlComment) {
      return this.resolveSourceMapUrl(urlComment, fileUrl);
    }
    return null;
  }

  extractFromComment(content) {
    const patterns = [
      /\/\/# sourceMappingURL=(.+?)(?:\n|$)/,
      /\/\*# sourceMappingURL=(.+?)\*\//
    ];

    for (const pattern of patterns) {
      const match = content.match(pattern);
      if (match) {
        return match[1].trim();
      }
    }
    return null;
  }

  resolveSourceMapUrl(mapUrl, jsFileUrl) {
    if (mapUrl.startsWith('data:')) {
      return mapUrl;
    }
    if (mapUrl.startsWith('http://') || mapUrl.startsWith('https://')) {
      return mapUrl;
    }
    try {
      const baseUrl = jsFileUrl.substring(0, jsFileUrl.lastIndexOf('/') + 1);
      return new URL(mapUrl, baseUrl).href;
    } catch (e) {
      return mapUrl;
    }
  }
}
