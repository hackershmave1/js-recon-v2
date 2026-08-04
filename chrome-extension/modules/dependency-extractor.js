export class DependencyExtractor {
  extract(content, baseUrl) {
    const dependencies = new Set();

    this.extractWebpackDeps(content, dependencies);
    this.extractESModuleImports(content, dependencies);
    this.extractDynamicImports(content, dependencies);
    this.extractRequireCalls(content, dependencies);

    return Array.from(dependencies).map(dep => ({
      url: dep,
      type: this.inferDependencyType(dep),
      resolvedUrl: this.resolveUrl(dep, baseUrl)
    }));
  }

  extractWebpackDeps(content, dependencies) {
    const depsPattern = /window\[['"]([^'"]+)Deps['"]\]\s*=\s*\[(.*?)\]/g;
    let match;
    
    while ((match = depsPattern.exec(content)) !== null) {
      const depsArray = match[2];
      const paths = depsArray.match(/["']([^"']+)["']/g);
      
      if (paths) {
        paths.forEach(path => {
          dependencies.add(path.replace(/["']/g, ''));
        });
      }
    }

    // NOTE: numeric-chunk-URL guessing was removed here. It matched a bare `e(123)`
    // call — ubiquitous in minified code — and invented `/static/js/123.chunk.js` plus
    // `/chunks/123.js`, which 404 en masse on any bundler that content-hashes chunk
    // names (webpack 5, Module Federation) and computes the public path at runtime.
    // The guesses never resolved to real files; they only flooded the queue and the
    // Errors panel with 404s. Real chunks are discovered from actual script loads
    // (webRequest) and the explicit dep arrays parsed above.
  }

  extractESModuleImports(content, dependencies) {
    const staticImportPattern = /import\s+(?:[\w{},\s*]*)\s+from\s+['"]([^'"]+)['"]/g;
    let match;
    
    while ((match = staticImportPattern.exec(content)) !== null) {
      dependencies.add(match[1]);
    }
  }

  extractDynamicImports(content, dependencies) {
    const dynamicImportPattern = /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
    let match;
    
    while ((match = dynamicImportPattern.exec(content)) !== null) {
      dependencies.add(match[1]);
    }
  }

  extractRequireCalls(content, dependencies) {
    const requirePattern = /require\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
    let match;
    
    while ((match = requirePattern.exec(content)) !== null) {
      dependencies.add(match[1]);
    }
  }

  inferDependencyType(path) {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return 'absolute';
    }
    if (path.startsWith('/')) {
      return 'root-relative';
    }
    if (path.startsWith('./') || path.startsWith('../')) {
      return 'relative';
    }
    return 'package';
  }

  resolveUrl(path, baseUrl) {
    try {
      if (path.startsWith('http://') || path.startsWith('https://')) {
        return path;
      }
      const base = new URL(baseUrl);
      if (path.startsWith('/')) {
        return `${base.protocol}//${base.host}${path}`;
      }
      if (path.startsWith('./') || path.startsWith('../')) {
        const baseDir = baseUrl.substring(0, baseUrl.lastIndexOf('/') + 1);
        return new URL(path, baseDir).href;
      }
      return new URL(path, base).href;
    } catch (e) {
      return path;
    }
  }
}
