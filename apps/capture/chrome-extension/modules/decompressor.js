export class Decompressor {
  async decompress(content, encoding) {
    encoding = encoding.toLowerCase();

    try {
      if (encoding === 'gzip' || encoding === 'x-gzip') {
        return await this.decompressGzip(content);
      } else if (encoding === 'br') {
        return await this.decompressBrotli(content);
      } else if (encoding === 'deflate') {
        return await this.decompressDeflate(content);
      }

      return { success: true, content: content };
    } catch (error) {
      return { success: false, error: error.message };
    }
  }

  async decompressGzip(buffer) {
    try {
      const stream = new Blob([buffer]).stream();
      const decompressedStream = stream.pipeThrough(
        new DecompressionStream('gzip')
      );
      
      const decompressedBlob = await new Response(decompressedStream).blob();
      const text = await decompressedBlob.text();

      return { success: true, content: text, originalEncoding: 'gzip' };
    } catch (error) {
      throw new Error(`Gzip decompression failed: ${error.message}`);
    }
  }

  async decompressBrotli(buffer) {
    try {
      const stream = new Blob([buffer]).stream();
      const decompressedStream = stream.pipeThrough(
        new DecompressionStream('br')
      );
      
      const decompressedBlob = await new Response(decompressedStream).blob();
      const text = await decompressedBlob.text();

      return { success: true, content: text, originalEncoding: 'br' };
    } catch (error) {
      throw new Error(`Brotli decompression failed: ${error.message}`);
    }
  }

  async decompressDeflate(buffer) {
    try {
      const stream = new Blob([buffer]).stream();
      const decompressedStream = stream.pipeThrough(
        new DecompressionStream('deflate')
      );
      
      const decompressedBlob = await new Response(decompressedStream).blob();
      const text = await decompressedBlob.text();

      return { success: true, content: text, originalEncoding: 'deflate' };
    } catch (error) {
      throw new Error(`Deflate decompression failed: ${error.message}`);
    }
  }
}
