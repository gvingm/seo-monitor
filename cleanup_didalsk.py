#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WP maintenance on didalsk.ru:
  1. Create 6 categories by clusters (if not exists)
  2. Cleanup 880 revisions via SQL
  3. Verify
"""
import paramiko, sys, io, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('31.31.198.144', 22, 'u3360387', '722WdHz37rxLa4XB', timeout=30)
def run(cmd, t=60):
    si, so, se = c.exec_command(cmd, timeout=t, get_pty=True)
    return so.read().decode('utf-8', errors='replace')

# 1. WP maintenance script
php = r'''<?php
chdir('/var/www/u3360387/data/www/didalsk.ru');
require '/var/www/u3360387/data/www/didalsk.ru/wp-load.php';

echo "=== 1. Create 6 categories by clusters ===\\n";
$clusters = [
    ['dnouglublenie',    'Дноуглубление',          'dredging',  'Дноуглубительные работы, речное и морское дноуглубление, углубление дна, расчистка русел.'],
    ['beregoukreplenie', 'Берегоукрепление',       'shore',     'Берегоукрепительные работы, укрепление берегов, шпунтовые ограждения, набережные.'],
    ['dyukery',          'Дюкеры и подводные переходы', 'duct',  'Протаскивание дюкеров, подводные переходы трубопроводов через реки.'],
    ['gidrotehnika',     'Гидротехнические работы','hydro',     'Гидротехническое строительство, водолазные работы, подводно-технические работы, обследования.'],
    ['arenda-tehniki',   'Аренда техники',          'rental',    'Аренда земснаряда, экскаватора на понтоне, шаланды, баржи, буксира, понтона.'],
    ['tehnicheskij-flot','Маломерный технический флот', 'fleet','Шаланды, баржи, буксиры, катера, понтоны — специализированный технический флот.'],
];
$created = 0; $existed = 0;
foreach ($clusters as [$slug, $name, $hint, $desc]) {
    $term = term_exists($slug, 'category');
    if ($term) { $existed++; echo "  exists: $slug (id=$term) -> $name\\n"; continue; }
    $new = wp_insert_term($name, 'category', [
        'slug' => $slug, 'description' => $desc,
    ]);
    if (is_wp_error($new)) { echo "  ERROR: $slug -> " . $new->get_error_message() . "\\n"; }
    else { $created++; echo "  created: $slug (id={$new['term_id']}) -> $name\\n"; }
}
echo "Created: $created, Existed: $existed\\n";

echo "\\n=== 2. Cleanup revisions ===\\n";
global $wpdb;
$rev_count = $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_type = 'revision'");
echo "Revisions before: $rev_count\\n";
if ($rev_count > 0) {
    $deleted = $wpdb->query("DELETE a, b FROM {$wpdb->posts} a LEFT JOIN {$wpdb->postmeta} b ON a.ID = b.post_id WHERE a.post_type = 'revision'");
    $after = $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_type = 'revision'");
    echo "Deleted: $deleted rows\\nRevisions after: $after\\n";
} else {
    echo "No revisions to clean\\n";
}

echo "\\n=== 3. Final: category list ===\\n";
$cats = get_categories(['hide_empty' => false]);
echo "Total categories: " . count($cats) . "\\n";
foreach ($cats as $cc) echo "  - {$cc->slug} (id={$cc->term_id}, posts={$cc->count}) -> {$cc->name}\\n";
'''
b = base64.b64encode(php.encode()).decode()
print(run('echo ' + b + ' | base64 -d > /tmp/wp_maint.php && php /tmp/wp_maint.php 2>&1', 60))

c.close()
print("\nDONE")
