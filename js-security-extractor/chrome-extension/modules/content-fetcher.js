export class ContentFetcher {
  constructor() {
    this.cache = new Map();
    this.maxRetries = 3;
  }

  async fetch(url, options = {}) {
    if (this.cache.has(url)) {
      return { success: true, content: this.cache.get(url), cached: true };
    }

    for (let attempt = 0; attempt < this.maxRetries; attempt++) {
      try {
        const response = await fetch(url, {
          method: 'GET',
          credentials: 'include',
          headers: options.headers || {}
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const contentEncoding = response.headers.get('content-encoding') || 'identity';
        const isCompressedFile =
          url.endsWith('.gz') ||
          url.endsWith('.br') ||
          url.endsWith('.deflate');

        if (isCompressedFile) {
          const buffer = await response.arrayBuffer();
          return {
            success: true,
            content: buffer,
            isBinary: true,
            contentEncoding: contentEncoding
          };
        }

        const content = await response.text();
        this.cache.set(url, content);
        return {
          success: true,
          content: content,
          contentEncoding: contentEncoding
        };
      } catch (error) {
        if (attempt === this.maxRetries - 1) {
          return { success: false, error: error.message };
        }
        await new Promise(resolve => setTimeout(resolve, Math.pow(2, attempt) * 1000));
      }
    }
  }
}
