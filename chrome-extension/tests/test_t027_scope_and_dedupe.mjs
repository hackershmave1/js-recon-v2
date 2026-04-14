#!/usr/bin/env node
/**
 * Test T-027: Tighten Capture Scope and Version-Aware Extension Deduping
 * Tests the improved domain matching and content-hash based deduplication.
 */

// Simple test framework
function assert(condition, message) {
  if (!condition) {
    throw new Error(`Assertion failed: ${message}`);
  }
}

// Mock JSExtractor class to test the logic
class MockJSExtractor {
  constructor() {
    this.capturedFiles = new Map();
    this.capturedHashes = new Map();
    this.settings = {
      useDomainScope: true,
      domainScopes: ['example.com', 'test.org']
    };
  }

  // Copy the exact isInScope implementation
  isInScope(url) {
    if (!this.settings.useDomainScope || 
        this.settings.domainScopes.length === 0) {
      return true;
    }
    
    try {
      const urlObj = new URL(url);
      const hostname = urlObj.hostname.toLowerCase();
      
      return this.settings.domainScopes.some(scope => {
        const trimmed = scope.trim().toLowerCase();
        if (!trimmed) return false;
        
        // Exact domain match
        if (hostname === trimmed) return true;
        
        // Subdomain match (must end with the scope domain)
        if (hostname.endsWith('.' + trimmed)) return true;
        
        return false;
      });
    } catch (e) {
      return false;
    }
  }

  async calculateHash(content) {
    // Simple hash for testing (not crypto)
    let hash = 0;
    for (let i = 0; i < content.length; i++) {
      const char = content.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32-bit integer
    }
    return Math.abs(hash).toString(16);
  }

  // Simplified version of the deduplication logic
  async checkDuplication(url, content) {
    const contentHash = await this.calculateHash(content);

    // Check if this URL was captured before
    const existingFile = this.capturedFiles.get(url);
    
    if (existingFile) {
      if (existingFile.contentHash === contentHash) {
        // Same URL, same content - duplicate
        return { isDuplicate: true, reason: 'same_url_same_content' };
      } else {
        // Same URL, different content - needs re-capture
        this.capturedHashes.delete(existingFile.contentHash);
        return { isDuplicate: false, reason: 'content_changed', oldHash: existingFile.contentHash, newHash: contentHash };
      }
    }
    
    // Check if we have this exact content from a different URL
    if (this.capturedHashes.has(contentHash)) {
      const existingCapture = this.capturedHashes.get(contentHash);
      return { isDuplicate: true, reason: 'same_content', existingUrl: existingCapture.url };
    }

    // New file - would be captured
    return { isDuplicate: false, reason: 'new_file', hash: contentHash };
  }

  async simulateCapture(url, content) {
    const contentHash = await this.calculateHash(content);
    const fileObject = {
      url: url,
      contentHash: contentHash,
      capturedAt: new Date().toISOString()
    };
    
    this.capturedFiles.set(url, fileObject);
    this.capturedHashes.set(contentHash, {url: url, capturedAt: fileObject.capturedAt});
  }
}

function testDomainScopeMatching() {
  console.log('🎯 Test 1: Domain scope matching...');
  
  const extractor = new MockJSExtractor();
  
  // Test exact domain matches
  assert(extractor.isInScope('https://example.com/app.js'), 'Should match exact domain');
  assert(extractor.isInScope('https://test.org/lib.js'), 'Should match second exact domain');
  
  // Test subdomain matches
  assert(extractor.isInScope('https://api.example.com/data.js'), 'Should match subdomain');
  assert(extractor.isInScope('https://cdn.test.org/bundle.js'), 'Should match subdomain of second domain');
  assert(extractor.isInScope('https://deep.sub.example.com/file.js'), 'Should match deep subdomain');
  
  // Test exclusions (over-broad matching prevented)
  assert(!extractor.isInScope('https://notexample.com/app.js'), 'Should NOT match similar domain');
  assert(!extractor.isInScope('https://examplenotcom.net/app.js'), 'Should NOT match domain prefix');
  assert(!extractor.isInScope('https://different.com/app.js'), 'Should NOT match unrelated domain');
  
  // Test with domain scope disabled
  extractor.settings.useDomainScope = false;
  assert(extractor.isInScope('https://anywhere.com/app.js'), 'Should match any domain when scope disabled');
  
  console.log('✅ Domain scope matching tests passed');
  return true;
}

async function testContentChangeDetection() {
  console.log('🔄 Test 2: Content change detection...');
  
  const extractor = new MockJSExtractor();
  const url = 'https://example.com/dynamic.js';
  
  // First content version
  const content1 = 'console.log("version 1");';
  const result1 = await extractor.checkDuplication(url, content1);
  assert(!result1.isDuplicate, 'First capture should not be duplicate');
  assert(result1.reason === 'new_file', `Expected 'new_file', got '${result1.reason}'`);
  
  // Simulate capturing the first version
  await extractor.simulateCapture(url, content1);
  
  // Same content again - should be deduplicated
  const result2 = await extractor.checkDuplication(url, content1);
  assert(result2.isDuplicate, 'Same content should be duplicate');
  assert(result2.reason === 'same_url_same_content', `Expected 'same_url_same_content', got '${result2.reason}'`);
  
  // Changed content at same URL - should be re-captured
  const content2 = 'console.log("version 2 - updated");';
  const result3 = await extractor.checkDuplication(url, content2);
  assert(!result3.isDuplicate, 'Changed content should not be duplicate');
  assert(result3.reason === 'content_changed', `Expected 'content_changed', got '${result3.reason}'`);
  
  console.log('✅ Content change detection tests passed');
  return true;
}

async function testHashBasedDeduplication() {
  console.log('📝 Test 3: Hash-based deduplication...');
  
  const extractor = new MockJSExtractor();
  const sharedContent = 'function shared() { return "library"; }';
  
  // Same content at different URLs
  const url1 = 'https://example.com/lib.js';
  const url2 = 'https://cdn.example.com/lib.js';
  
  // Capture first instance
  const result1 = await extractor.checkDuplication(url1, sharedContent);
  assert(!result1.isDuplicate, 'First instance should not be duplicate');
  await extractor.simulateCapture(url1, sharedContent);
  
  // Same content at different URL - should be deduplicated by hash
  const result2 = await extractor.checkDuplication(url2, sharedContent);
  assert(result2.isDuplicate, 'Same content at different URL should be duplicate');
  assert(result2.reason === 'same_content', `Expected 'same_content', got '${result2.reason}'`);
  assert(result2.existingUrl === url1, 'Should reference original URL');
  
  console.log('✅ Hash-based deduplication tests passed');
  return true;
}

function testEdgeCases() {
  console.log('🧪 Test 4: Edge cases...');
  
  const extractor = new MockJSExtractor();
  
  // Test invalid URLs
  assert(!extractor.isInScope('not-a-url'), 'Should handle invalid URL gracefully');
  assert(!extractor.isInScope(''), 'Should handle empty URL gracefully');
  
  // Test empty domain scopes
  extractor.settings.domainScopes = ['', ' ', 'valid.com'];
  assert(extractor.isInScope('https://valid.com/test.js'), 'Should work with empty scopes in list');
  assert(!extractor.isInScope('https://invalid.com/test.js'), 'Should not match when only valid domain in list');
  
  // Test case sensitivity
  assert(extractor.isInScope('https://Valid.Com/test.js'), 'Should be case insensitive');
  assert(extractor.isInScope('https://SUB.valid.com/test.js'), 'Should be case insensitive for subdomains');
  
  console.log('✅ Edge case tests passed');
  return true;
}

async function runAllTests() {
  console.log('🔍 Testing T-027: Scope and Deduplication Improvements');
  console.log('=' * 55);
  
  try {
    const results = await Promise.all([
      testDomainScopeMatching(),
      testContentChangeDetection(),
      testHashBasedDeduplication(),
      testEdgeCases()
    ]);
    
    const allPassed = results.every(r => r === true);
    
    console.log('=' * 55);
    if (allPassed) {
      console.log('🎉 All T-027 tests passed');
      process.exit(0);
    } else {
      console.log('❌ Some T-027 tests failed');
      process.exit(1);
    }
  } catch (error) {
    console.log('❌ Test execution failed:', error.message);
    process.exit(1);
  }
}

runAllTests();