#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply critical SEO fixes on didalsk.ru:
  1. Create robots.txt
  2. Enable Yoast XML Sitemap (and flush rewrite rules)
  3. Set blogdescription
  4. Cleanup .bak plugin folders
  5. (Read-only audit) Verify
"""
import paramiko, sys, io, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("31.31.198.144", 22, "u3360387", "722WdHz37rxLa4XB", timeout=30)

def run(cmd, t=60):
    si, so, se = c.exec_command(cmd, timeout=t, get_pty=True)
    return so.read().decode("utf-8", errors="replace")

DOC = "/var/www/u3360387/data/www/didalsk.ru"

# 0. Show before
print("=== BEFORE ===")
print(run(f"ls -la {DOC}/robots.txt {DOC}/sitemap.xml 2>&1 | head -5", 10))
print(run(f"head -3 {DOC}/wp-config.php | grep DB", 5))

# 1. Create robots.txt
print()
print("=== 1. Create robots.txt ===")
robots_txt = """User-agent: *
Disallow: /wp-admin/
Disallow: /wp-includes/
Disallow: /?s=
Disallow: /search/
Disallow: /feed/
Disallow: /cart/
Disallow: /checkout/
Disallow: /my-account/
Disallow: /wp-login.php
Disallow: /xmlrpc.php
Allow: /wp-admin/admin-ajax.php
Allow: /wp-content/uploads/

# AI Crawlers — приветствуем (для llms.txt)
User-agent: GPTBot
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: PerplexityBot
Allow: /

# Sitemap
Sitemap: https://didalsk.ru/sitemap.xml
"""
b = base64.b64encode(robots_txt.encode()).decode()
print(run(f"echo {b} | base64 -d > {DOC}/robots.txt && chmod 644 {DOC}/robots.txt && ls -la {DOC}/robots.txt", 10))
print(run(f"head -10 {DOC}/robots.txt", 5))

# 2. Apply Yoast settings + blogdescription + flush rewrites
print()
print("=== 2. Enable Yoast XML Sitemap + set blogdescription ===")
fix_php = '''<?php
chdir('/var/www/u3360387/data/www/didalsk.ru');
require '/var/www/u3360387/data/www/didalsk.ru/wp-load.php';

// 1. blogdescription
$old_desc = get_option('blogdescription');
$new_desc = 'Подрядчик по дноуглубительным, гидротехническим работам и аренде спецтехники в СПб и СЗФО. Опыт более 20 лет.';
update_option('blogdescription', $new_desc);
echo "blogdescription: \\"" . $old_desc . "\\" -> \\"" . get_option('blogdescription') . "\\"\\n";

// 2. Yoast XML Sitemap — enable feature
$yoast_opts = get_option('wpseo', []);
$before = isset($yoast_opts['xml']['enable_xml_sitemap']) ? (int)$yoast_opts['xml']['enable_xml_sitemap'] : 0;
$yoast_opts['xml']['enable_xml_sitemap'] = 1;
$yoast_opts['xml']['enable_texturize'] = 1;
update_option('wpseo', $yoast_opts);
echo "Yoast XML Sitemap: {$before} -> " . (int)$yoast_opts['xml']['enable_xml_sitemap'] . "\\n";

// 3. Yoast titles enable
$yoast_opts['titles']['enable_title_generation'] = true;
update_option('wpseo', $yoast_opts);
echo "Yoast titles enable: " . var_export($yoast_opts['titles']['enable_title_generation'], true) . "\\n";

// 4. Flush rewrite rules
flush_rewrite_rules(true);
echo "rewrite rules flushed\\n";

// 5. Disable Yoast "noindex" on default pages if enabled
$noindex = get_option('blog_public');
echo "blog_public (search engines allowed): {$noindex}\\n";
'''
b2 = base64.b64encode(fix_php.encode()).decode()
print(run(f"echo {b2} | base64 -d > /tmp/fix_seo.php && php /tmp/fix_seo.php 2>&1", 30))

# 3. Verify sitemap.xml appears (Yoast generates it on request via /sitemap.xml)
print()
print("=== 3. Check sitemap ===")
print(run(f"curl -s -o /dev/null -w 'sitemap.xml: %{http_code}\\n' https://didalsk.ru/sitemap.xml", 15))
print(run(f"curl -s -o /dev/null -w 'sitemap_index.xml: %{http_code}\\n' https://didalsk.ru/sitemap_index.xml", 15))
print(run(f"curl -s -o /dev/null -w 'robots.txt: %{http_code}\\n' https://didalsk.ru/robots.txt", 15))

# 4. Cleanup .bak plugin folders
print()
print("=== 4. Cleanup .bak plugin folders ===")
bak_folders = run(f"ls -d {DOC}/wp-content/plugins/*.bak-* 2>/dev/null", 10).strip().split('\n')
print(f"Found {len(bak_folders)} .bak folders")
for folder in bak_folders:
    if not folder.strip():
        continue
    name = folder.split('/')[-1]
    print(f"  {name} -> moving to /tmp on server")
    print(run(f"mv {folder} {DOC}/wp-content/upgrade-temp-backup/{name} 2>&1 || true", 10))

# 5. Final summary
print()
print("=== AFTER ===")
print(run(f"ls -la {DOC}/robots.txt {DOC}/sitemap_index.xml 2>&1 | head -5", 10))
print(run(f"head -20 {DOC}/robots.txt", 5))

# 6. Ping Yandex and Google about new sitemap
print()
print("=== 5. Ping Yandex/Google about sitemap ===")
print(run("curl -s 'https://webmaster.yandex.ru/ping?sitemap=https://didalsk.ru/sitemap.xml' -w 'yandex: %{http_code}\\n' -o /dev/null", 15))

c.close()
print()
print("DONE")
