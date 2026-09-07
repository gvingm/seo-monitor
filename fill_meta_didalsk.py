#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mass-fill Yoast meta title/description for didalsk.ru pages without meta.

For each post without _yoast_wpseo_title / _yoast_wpseo_metadesc:
  - Generate title:  "{post_title} — Дидал-СК"  (or use existing title if better)
  - Generate desc:   "{post_title}. Дноуглубительные и гидротехнические работы в СПб и СЗФО. Опыт более 20 лет. Тел. +7 969 200-57-68."
  - Focus kw:       infer from post_title (lowercase, first significant word or 2)

Skips: drafts, trash, autosaves, revisions.
Writes: only if no existing meta (don't overwrite).
"""
import paramiko, sys, io, base64
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("31.31.198.144", 22, "u3360387", "722WdHz37rxLa4XB", timeout=30)
def run(cmd, t=120):
    si, so, se = c.exec_command(cmd, timeout=t, get_pty=True)
    return so.read().decode("utf-8", errors="replace")

php = r'''<?php
chdir('/var/www/u3360387/data/www/didalsk.ru');
require '/var/www/u3360387/data/www/didalsk.ru/wp-load.php';
global $wpdb;

$PHONE = '+7 969 200-57-68';
$SUFFIX = 'Дидал-СК';
$BASE_DESC = 'Дноуглубительные и гидротехнические работы в СПб и СЗФО. Опыт более 20 лет.';
$STOPWORDS = ['и','в','на','с','со','по','для','от','до','из','за','к','о','об','а','но','что','это','как','так','же','или','не','ни','быть','есть','этот','тот','при','без','под','над','между','через','перед'];
$MAX_TITLE = 60;
$MAX_DESC = 160;

$posts = $wpdb->get_results("
    SELECT p.ID, p.post_title, p.post_type, p.post_status
    FROM {$wpdb->posts} p
    WHERE p.post_status = 'publish'
      AND p.post_type IN ('page','post','didalsk_project','didalsk_product','didalsk_rental')
      AND NOT EXISTS (
        SELECT 1 FROM {$wpdb->postmeta} pm
        WHERE pm.post_id = p.ID AND pm.meta_key = '_yoast_wpseo_title' AND pm.meta_value <> ''
      )
    ORDER BY p.post_type, p.ID
");

echo "Posts to update: " . count($posts) . "\n";

$updated = 0; $skipped = 0;
foreach ($posts as $p) {
    $title = trim($p->post_title);
    if ($title === '') { $skipped++; continue; }

    // SEO title: "{title} — Дидал-СК", truncate
    $seo_title = $title . ' — ' . $SUFFIX;
    if (mb_strlen($seo_title) > $MAX_TITLE) {
        // Урезаем title до (MAX_TITLE - suffix - 3) с многоточием
        $space_for = $MAX_TITLE - mb_strlen(' — ' . $SUFFIX);
        $seo_title = mb_substr($title, 0, $space_for - 1) . '… — ' . $SUFFIX;
    }

    // Description
    $desc = $title . '. ' . $BASE_DESC . ' ' . $PHONE . '.';
    if (mb_strlen($desc) > $MAX_DESC) {
        $space_for_desc = $MAX_DESC - mb_strlen(' ' . $PHONE . '.');
        $desc = mb_substr($title . '. ' . $BASE_DESC, 0, $space_for_desc - 1) . '… ' . $PHONE . '.';
    }

    // Focus keyword: первое содержательное слово из title
    $words = preg_split('/\s+/u', mb_strtolower($title));
    $focus = '';
    foreach ($words as $w) {
        $w = preg_replace('/[^а-яa-zё0-9]/u', '', $w);
        if (mb_strlen($w) >= 3 && !in_array($w, $STOPWORDS)) {
            $focus = $w;
            break;
        }
    }
    if ($focus === '') $focus = mb_strtolower(mb_substr($title, 0, 30));

    // Write
    update_post_meta($p->ID, '_yoast_wpseo_title', $seo_title);
    update_post_meta($p->ID, '_yoast_wpseo_metadesc', $desc);
    if ($focus !== '') {
        update_post_meta($p->ID, '_yoast_wpseo_focuskw', $focus);
    }
    $updated++;
}

echo "Updated: $updated\nSkipped: $skipped\n";

// Sanity: check counts
$t = $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->postmeta} WHERE meta_key = '_yoast_wpseo_title' AND meta_value <> ''");
$d = $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->postmeta} WHERE meta_key = '_yoast_wpseo_metadesc' AND meta_value <> ''");
$f = $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->postmeta} WHERE meta_key = '_yoast_wpseo_focuskw' AND meta_value <> ''");
echo "After: titles=$t descs=$d focuskw=$f\n";
'''
b = base64.b64encode(php.encode()).decode()
print(run('echo ' + b + ' | base64 -d > /tmp/fill_meta.php && php /tmp/fill_meta.php 2>&1', 120))

c.close()
