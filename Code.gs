/**
 * ==============================================================================
 * CODE.GS — SYSTÈME AUTOMATISÉ AUSBILDUNG KAUFMANN / KAUFFRAU
 * Google Apps Script pour Google Sheet "Ausbildung applications"
 *
 * FONCTIONS PRINCIPALES :
 *   doPost(e)                        → Webhook : reçoit les offres du scraper Python
 *   traiterAusbildungCandidatures()  → Envoi emails + relances 48h (batch 20/run)
 *   getCV(intitule, roleCible)        → Mapping 5 CVs par spécialité Kauffrau
 *   configurerDeclencheurs()          → Installe le trigger horaire automatique
 *
 * MAPPING CV (fichiers confirmés sur Google Drive) :
 *   Büromanagement → "Bewerbung Kauffrau Buromanagemenet Halima Essaouaf.pdf"
 *   E-Commerce     → "Bewerbung Kauffrau ECommerce Halima Essaouaf.pdf"
 *   Groß/Außenhandel → "Bewerbung Kauffrau GrossAussenhandel Halima Essaouaf.pdf"
 *   Spedition      → "Bewerbung Kauffrau Spedition Logistik Halima Essaouaf.pdf"
 *   Tourismus      → "Bewerbung Kauffrau Tourismus Freizeit Halima Essaouaf.pdf"
 * ==============================================================================
 */

// ─────────────────────────────────────────────────────────────────────────────
// CONFIGURATION — À PERSONNALISER
// ─────────────────────────────────────────────────────────────────────────────

const CONFIG = {
  // Identité complète pour les emails
  NOM:         "Halima Essaouaf",
  EMAIL:       "essaouafhalima@gmail.com",
  TEL:         "+33 X XX XX XX XX",     // ← À compléter
  LINKEDIN:    "linkedin.com/in/halima-essaouaf",  // ← À compléter

  // Nom de l'onglet dans le Google Sheet (fallback sur onglet actif)
  NOM_ONGLET:  "Ausbildung",

  // Quota d'envoi par exécution (protection anti-spam Gmail)
  BATCH_LIMIT: 20,

  // Délai de relance en heures
  DELAI_RELANCE_H: 48,

  // Mapping EXACT des noms de fichiers CV sur Google Drive
  CV_MAPPING: {
    "buero":      "Bewerbung Kauffrau Buromanagemenet Halima Essaouaf.pdf",
    "ecommerce":  "Bewerbung Kauffrau ECommerce Halima Essaouaf.pdf",
    "handel":     "Bewerbung Kauffrau GrossAussenhandel Halima Essaouaf.pdf",
    "spedition":  "Bewerbung Kauffrau Spedition Logistik Halima Essaouaf.pdf",
    "tourismus":  "Bewerbung Kauffrau Tourismus Freizeit Halima Essaouaf.pdf",
  },
};

// Colonnes du Google Sheet (0-indexées)
const COL = {
  DATE_DETECTION: 0,  // A
  STATUT:         1,  // B
  ROLE_CIBLE:     2,  // C
  INTITULE:       3,  // D
  ENTREPRISE:     4,  // E
  LIEU:           5,  // F
  EMAILS_RH:      6,  // G
  SOURCE:         7,  // H
  LIEN:           8,  // I
  ID:             9,  // J
  DATE_ENVOI:    10,  // K
  DATE_RELANCE:  11,  // L
};


// ─────────────────────────────────────────────────────────────────────────────
// UTILITAIRES
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Retourne l'onglet Ausbildung, ou l'onglet actif si introuvable.
 */
function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(CONFIG.NOM_ONGLET) || ss.getActiveSheet();
}

/**
 * Valide qu'un email est utilisable (contient "@" et n'est pas un placeholder).
 */
function isValidEmail(email) {
  if (!email) return false;
  const str = String(email).trim();
  return (
    str.includes("@") &&
    !str.toLowerCase().includes("non détecté") &&
    !str.toLowerCase().includes("postuler via lien") &&
    /^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(str.split(" / ")[0].trim())
  );
}

/**
 * Extrait le premier email valide d'une cellule (peut contenir "email1 / email2").
 */
function extractFirstEmail(emailsRh) {
  if (!emailsRh) return null;
  const candidates = String(emailsRh).split(/\s*\/\s*/);
  for (const candidate of candidates) {
    const clean = candidate.replace(/^[u003e>"'\\]+/, "").trim();
    if (/^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(clean)) {
      return clean;
    }
  }
  return null;
}

/**
 * Formate une date en "DD.MM.YYYY HH:MM" (format allemand).
 */
function formatDateDE(date) {
  const d = new Date(date);
  const pad = n => String(n).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}


// ─────────────────────────────────────────────────────────────────────────────
// WEBHOOK doPost — RÉCEPTION DES OFFRES DEPUIS LE SCRAPER PYTHON
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Point d'entrée du webhook HTTP POST.
 * Appelé automatiquement par ausbildung_scraper.py via GOOGLE_SHEET_WEBHOOK_URL.
 * Insère uniquement les nouvelles offres (déduplication par ID colonne J).
 */
function doPost(e) {
  try {
    const rawData = JSON.parse(e.postData.contents);
    const jobs = Array.isArray(rawData) ? rawData : [rawData];

    const sheet = getSheet();
    const allData = sheet.getDataRange().getValues();

    // Construire le Set des IDs déjà présents (Col J = index 9)
    const existingIds = new Set();
    for (let i = 1; i < allData.length; i++) {
      const id = String(allData[i][COL.ID] || "").trim();
      if (id) existingIds.add(id);
    }

    let added = 0;

    for (const job of jobs) {
      const jobId = String(job.id || "").trim();

      // Vérification doublon
      if (!jobId || existingIds.has(jobId)) continue;

      // Vérification email valide (FILTRE STRICT)
      if (!isValidEmail(job.emails_rh)) continue;

      sheet.appendRow([
        job.date_detection  || new Date().toISOString(),
        job.statut          || "NOUVEAU",
        job.role_cible      || "",
        job.intitule        || "",
        job.entreprise      || "",
        job.lieu            || "Deutschland (Allemagne)",
        job.emails_rh       || "",
        job.source          || "Scraper Cloud Ausbildung",
        job.lien            || "",
        jobId,
        "",  // Col K : date_envoi (vide au départ)
        "",  // Col L : date_relance (vide au départ)
      ]);

      existingIds.add(jobId);
      added++;
    }

    Logger.log(`[WEBHOOK] ${added} nouvelles offres ajoutées sur ${jobs.length} reçues.`);

    return ContentService
      .createTextOutput(JSON.stringify({ status: "success", added: added }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    Logger.log("[WEBHOOK] Erreur: " + error.toString());
    return ContentService
      .createTextOutput(JSON.stringify({ status: "error", message: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// MAPPING CV — SÉLECTION PAR SPÉCIALITÉ KAUFFRAU
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Détermine la clé de spécialité à partir du contenu de l'intitulé et du rôle cible.
 * Retourne l'une des clés : "buero" | "ecommerce" | "handel" | "spedition" | "tourismus"
 */
function detecterSpecialite(intitule, roleCible) {
  const text = ((intitule || "") + " " + (roleCible || "")).toLowerCase();

  if (text.includes("e-commerce") || text.includes("ecommerce") || text.includes("e commerce")) {
    return "ecommerce";
  }
  if (
    text.includes("groß") || text.includes("gross") ||
    text.includes("außenhandel") || text.includes("aussenhandel") ||
    text.includes("außenhandelskaufmann")
  ) {
    return "handel";
  }
  if (text.includes("spedition") || text.includes("logistik") || text.includes("speditionskaufmann")) {
    return "spedition";
  }
  if (
    text.includes("tourismus") || text.includes("freizeit") ||
    text.includes("reisebüro") || text.includes("reisebuero") || text.includes("reiseverkehr")
  ) {
    return "tourismus";
  }
  // Défaut → Büromanagement
  return "buero";
}

/**
 * Titre formel de l'Ausbildung selon la spécialité détectée.
 */
function getTitreAusbildung(specialite) {
  const titres = {
    "buero":      "Kauffrau für Büromanagement",
    "ecommerce":  "Kauffrau im E-Commerce",
    "handel":     "Kauffrau im Groß- und Außenhandelsmanagement",
    "spedition":  "Kauffrau für Spedition und Logistikdienstleistung",
    "tourismus":  "Kauffrau für Tourismus und Freizeit",
  };
  return titres[specialite] || "Kauffrau für Büromanagement";
}

/**
 * Recherche et retourne le fichier CV PDF correct depuis Google Drive.
 * Utilise le mapping CONFIG.CV_MAPPING avec les noms de fichiers exacts confirmés.
 * Fallback sur le CV Büromanagement si le CV spécifique est introuvable.
 */
function getCV(intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const filename   = CONFIG.CV_MAPPING[specialite];

  Logger.log(`[CV] Spécialité détectée: ${specialite} → Fichier: ${filename}`);

  // Recherche principale
  let files = DriveApp.getFilesByName(filename);
  if (files.hasNext()) {
    Logger.log(`[CV] Trouvé: ${filename}`);
    return files.next();
  }

  // Fallback : CV Büromanagement (CV par défaut)
  const fallbackFile = CONFIG.CV_MAPPING["buero"];
  Logger.log(`[CV] '${filename}' introuvable → Fallback sur '${fallbackFile}'`);
  const fallback = DriveApp.getFilesByName(fallbackFile);
  if (fallback.hasNext()) {
    return fallback.next();
  }

  Logger.log("[CV] ERREUR : Aucun CV Ausbildung trouvé sur le Drive !");
  return null;
}


// ─────────────────────────────────────────────────────────────────────────────
// GÉNÉRATION DES EMAILS ALLEMANDS PAR SPÉCIALITÉ
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Génère la signature HTML complète (utilisée dans les emails HTML).
 */
function getSignatureHTML() {
  return `
<br><br>
<table style="font-family: Arial, sans-serif; font-size: 13px; color: #333; border-top: 2px solid #1a73e8; padding-top: 8px;">
  <tr>
    <td>
      <strong style="font-size: 14px; color: #1a1a1a;">${CONFIG.NOM}</strong><br>
      <span style="color: #666;">Bewerberin – Ausbildung Kauffrau</span><br><br>
      📧 <a href="mailto:${CONFIG.EMAIL}" style="color: #1a73e8;">${CONFIG.EMAIL}</a><br>
      📞 ${CONFIG.TEL}<br>
      🔗 <a href="https://${CONFIG.LINKEDIN}" style="color: #1a73e8;">${CONFIG.LINKEDIN}</a>
    </td>
  </tr>
</table>`.trim();
}

/**
 * Génère le corps HTML de l'email de candidature initiale.
 * Adapté par spécialité avec des formulations professionnelles en allemand.
 */
function genererEmailCandidature(entreprise, intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);
  const entrepriseDisplay = (entreprise && entreprise !== "Unternehmen Deutschland") ? entreprise : null;

  const salutation = entrepriseDisplay
    ? `Sehr geehrte Damen und Herren des Unternehmens ${entrepriseDisplay},`
    : "Sehr geehrte Damen und Herren,";

  // Corps de motivation adapté par spécialité
  const motivationParSpecialite = {
    "buero": `
mit großem Interesse und Begeisterung bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Ich bin eine organisierte, engagierte und kommunikationsstarke Persönlichkeit, die Freude daran hat, Abläufe zu optimieren, Aufgaben strukturiert zu erledigen und ein verlässlicher Teil eines Teams zu sein. Die Ausbildung zum Kaufmann/-frau für Büromanagement entspricht genau meinen Stärken und Interessen: der Umgang mit Kunden, das Koordinieren von Aufgaben und die Arbeit in einem modernen Büroumfeld.`,

    "ecommerce": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Der digitale Handel fasziniert mich sehr: von der Produktpräsentation über den Onlineshop bis hin zur Kundenkommunikation. Ich bin technikaffin, lernbereit und bringe bereits erste Kenntnisse im Online-Marketing und in der Arbeit mit digitalen Tools mit. Ich möchte mein Wissen in Ihrem Unternehmen vertiefen und aktiv zu Ihrem Wachstum im E-Commerce beitragen.`,

    "handel": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Die Welt des nationalen und internationalen Handels fasziniert mich sehr – insbesondere die Arbeit mit Lieferanten, die Koordination von Warenflüssen und die Kommunikation auf internationaler Ebene. Ich bin kommunikativ, zahlenaffin und spreche mehrere Sprachen, was mir im Groß- und Außenhandel einen echten Vorteil verschafft.`,

    "spedition": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Logistik und die Organisation von Transporten begeistern mich: Güter effizient von A nach B zu bringen, Lieferketten zu koordinieren und mit nationalen wie internationalen Partnern zusammenzuarbeiten – das ist genau das Berufsfeld, in dem ich mich langfristig entwickeln möchte. Ich bin belastbar, strukturiert und teamorientiert.`,

    "tourismus": `
mit großem Interesse bewerbe ich mich um einen Ausbildungsplatz als <strong>${titrePoste}</strong> in Ihrem Unternehmen.

Die Tourismusbranche begeistert mich durch ihre Vielseitigkeit und den täglichen Kontakt mit Menschen aus aller Welt. Ich bin serviceorientiert, kommunikativ, spreche mehrere Sprachen und bringe echte Freude daran mit, unvergessliche Reiseerlebnisse für Kunden zu gestalten.`,
  };

  const motivation = motivationParSpecialite[specialite] || motivationParSpecialite["buero"];

  const body = `
${salutation}
${motivation}

Anbei finden Sie meine vollständigen Bewerbungsunterlagen (Lebenslauf) als PDF-Datei. Ich freue mich sehr auf die Möglichkeit, mich in einem persönlichen Gespräch – telefonisch oder per Video-Call – vorzustellen und mehr über Ihre Ausbildung zu erfahren.

Über eine positive Rückmeldung würde ich mich sehr freuen.

Mit freundlichen Grüßen
${getSignatureHTML()}
`.trim();

  return { titrePoste, body };
}

/**
 * Génère l'email de relance 48h en allemand professionnel.
 */
function genererEmailRelance(entreprise, intitule, roleCible) {
  const specialite = detecterSpecialite(intitule, roleCible);
  const titrePoste = getTitreAusbildung(specialite);
  const entrepriseDisplay = (entreprise && entreprise !== "Unternehmen Deutschland") ? entreprise : null;

  const salutation = entrepriseDisplay
    ? `Sehr geehrte Damen und Herren des Unternehmens ${entrepriseDisplay},`
    : "Sehr geehrte Damen und Herren,";

  const body = `
${salutation}

vor zwei Tagen habe ich Ihnen meine Bewerbungsunterlagen für den Ausbildungsplatz als <strong>${titrePoste}</strong> zugesendet und möchte mich kurz erkundigen, ob diese gut bei Ihnen eingegangen sind.

Da ich nach wie vor großes Interesse an einer Ausbildung in Ihrem Unternehmen habe, erlauben Sie mir, meinen Lebenslauf zur Sicherheit erneut beizufügen.

Ich stehe Ihnen jederzeit für ein erstes Gespräch zur Verfügung und freue mich sehr auf Ihre Rückmeldung.

Mit freundlichen Grüßen
${getSignatureHTML()}
`.trim();

  return { titrePoste, body };
}


// ─────────────────────────────────────────────────────────────────────────────
// FONCTION PRINCIPALE — ENVOIS & RELANCES
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Traite toutes les lignes du Google Sheet Ausbildung :
 *   - Statut "NOUVEAU"             → Envoie candidature initiale + CV
 *   - Statut "CANDIDATURE_ENVOYEE" → Envoie relance 48h si délai atteint
 * Maximum CONFIG.BATCH_LIMIT envois par exécution.
 */
function traiterAusbildungCandidatures() {
  const sheet = getSheet();
  const data  = sheet.getDataRange().getValues();
  const now   = new Date();

  Logger.log(`\n${"=".repeat(60)}`);
  Logger.log(` TRAITEMENT AUSBILDUNG — ${formatDateDE(now)}`);
  Logger.log(` Feuille: "${sheet.getName()}" — ${data.length - 1} ligne(s)`);
  Logger.log(`${"=".repeat(60)}\n`);

  if (data.length <= 1) {
    Logger.log("⚠️ Feuille vide ou en-tête uniquement. Rien à traiter.");
    return;
  }

  let compteur = 0;

  for (let i = 1; i < data.length; i++) {
    if (compteur >= CONFIG.BATCH_LIMIT) {
      Logger.log(`🛑 Quota de ${CONFIG.BATCH_LIMIT} envois atteint pour cette exécution.`);
      break;
    }

    const row        = data[i];
    const statut     = String(row[COL.STATUT]     || "").trim();
    const roleCible  = String(row[COL.ROLE_CIBLE] || "").trim();
    const intitule   = String(row[COL.INTITULE]   || "").trim();
    const entreprise = String(row[COL.ENTREPRISE] || "").trim() || "Unternehmen Deutschland";
    const emailsRh   = String(row[COL.EMAILS_RH]  || "").trim();
    const dateEnvoi  = row[COL.DATE_ENVOI] ? new Date(row[COL.DATE_ENVOI]) : null;
    const rowNum     = i + 1;

    // ── Filtre email valide ────────────────────────────────────────────────
    if (!isValidEmail(emailsRh)) {
      Logger.log(`⏭️ Ligne ${rowNum} ignorée : email invalide ou absent ('${emailsRh}')`);
      continue;
    }

    const emailCible = extractFirstEmail(emailsRh);
    if (!emailCible) {
      Logger.log(`⏭️ Ligne ${rowNum} ignorée : impossible d'extraire un email propre`);
      continue;
    }

    // ── CAS 1 : CANDIDATURE INITIALE ─────────────────────────────────────
    if (statut === "NOUVEAU") {
      const cvFile = getCV(intitule, roleCible);

      if (!cvFile) {
        Logger.log(`⚠️ Ligne ${rowNum} ignorée : CV introuvable pour "${intitule}"`);
        continue;
      }

      const { titrePoste, body } = genererEmailCandidature(entreprise, intitule, roleCible);
      const sujet = `Bewerbung um einen Ausbildungsplatz als ${titrePoste} – ${CONFIG.NOM}`;

      try {
        GmailApp.sendEmail(emailCible, sujet, "", {
          htmlBody: body,
          attachments: [cvFile.getAs(MimeType.PDF)],
          name: CONFIG.NOM,
          replyTo: CONFIG.EMAIL,
        });

        sheet.getRange(rowNum, COL.STATUT + 1).setValue("CANDIDATURE_ENVOYEE");
        sheet.getRange(rowNum, COL.DATE_ENVOI + 1).setValue(new Date());
        compteur++;

        Logger.log(`✅ CANDIDATURE ENVOYÉE (ligne ${rowNum})`);
        Logger.log(`   → Entreprise : ${entreprise}`);
        Logger.log(`   → Email      : ${emailCible}`);
        Logger.log(`   → CV joint   : ${cvFile.getName()}`);

      } catch (err) {
        Logger.log(`❌ ERREUR ENVOI (ligne ${rowNum} / ${emailCible}): ${err.toString()}`);
      }
    }

    // ── CAS 2 : RELANCE 48H ───────────────────────────────────────────────
    else if (statut === "CANDIDATURE_ENVOYEE" && dateEnvoi) {
      const diffHeures = (now - dateEnvoi) / (1000 * 60 * 60);

      if (diffHeures < CONFIG.DELAI_RELANCE_H) {
        Logger.log(`⏳ Ligne ${rowNum} : Envoyée il y a ${Math.round(diffHeures)}h (relance à ${CONFIG.DELAI_RELANCE_H}h)`);
        continue;
      }

      const cvFile = getCV(intitule, roleCible);
      const { titrePoste, body } = genererEmailRelance(entreprise, intitule, roleCible);
      const sujet = `Nachfassaktion – Bewerbung als ${titrePoste} – ${CONFIG.NOM}`;

      try {
        GmailApp.sendEmail(emailCible, sujet, "", {
          htmlBody: body,
          attachments: cvFile ? [cvFile.getAs(MimeType.PDF)] : [],
          name: CONFIG.NOM,
          replyTo: CONFIG.EMAIL,
        });

        sheet.getRange(rowNum, COL.STATUT + 1).setValue("RELANCE_EFFECTUEE");
        sheet.getRange(rowNum, COL.DATE_RELANCE + 1).setValue(new Date());
        compteur++;

        Logger.log(`🔄 RELANCE 48H EFFECTUÉE (ligne ${rowNum})`);
        Logger.log(`   → Entreprise : ${entreprise}`);
        Logger.log(`   → Email      : ${emailCible}`);
        Logger.log(`   → Délai réel : ${Math.round(diffHeures)}h`);

      } catch (err) {
        Logger.log(`❌ ERREUR RELANCE (ligne ${rowNum} / ${emailCible}): ${err.toString()}`);
      }
    }
  }

  Logger.log(`\n${"=".repeat(60)}`);
  Logger.log(` FIN DU TRAITEMENT — ${compteur} email(s) envoyé(s) cette session`);
  Logger.log(`${"=".repeat(60)}\n`);
}


// ─────────────────────────────────────────────────────────────────────────────
// INSTALLATION DU TRIGGER HORAIRE — À APPELER UNE SEULE FOIS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Installe un déclencheur toutes les heures pour traiterAusbildungCandidatures().
 * À appeler UNE SEULE FOIS depuis l'éditeur Apps Script.
 * Supprime les anciens triggers du même nom pour éviter les doublons.
 */
function configurerDeclencheurs() {
  // Supprimer tous les triggers existants sur cette fonction
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === "traiterAusbildungCandidatures") {
      ScriptApp.deleteTrigger(trigger);
      Logger.log("[TRIGGER] Ancien trigger supprimé.");
    }
  });

  // Créer un nouveau trigger horaire
  ScriptApp.newTrigger("traiterAusbildungCandidatures")
    .timeBased()
    .everyHours(1)
    .create();

  Logger.log("✅ Trigger horaire installé : 'traiterAusbildungCandidatures' sera exécuté toutes les heures.");
  Logger.log("   → Le système enverra des candidatures et relances automatiquement 24h/24.");
}

/**
 * Affiche la liste des triggers actifs (diagnostic).
 */
function diagnosticTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  Logger.log(`\n[DIAGNOSTIC] ${triggers.length} trigger(s) actif(s) :`);
  triggers.forEach((t, i) => {
    Logger.log(`  ${i + 1}. ${t.getHandlerFunction()} — ${t.getTriggerSource()} — ID: ${t.getUniqueId()}`);
  });
}

/**
 * Test rapide : affiche le CV détecté pour un intitulé donné.
 * Utile pour vérifier le mapping sans envoyer d'email.
 */
function testerMappingCV() {
  const tests = [
    ["Ausbildung Kauffrau Büromanagement bei Siemens", "Kaufmann/-frau für Büromanagement"],
    ["Ausbildungsplatz E-Commerce 2026", "Kauffrau im E-Commerce"],
    ["Kaufmann im Groß- und Außenhandel", "Kaufmann/-frau im Groß- und Außenhandelsmanagement"],
    ["Ausbildung Speditionskaufmann Hamburg", "Kaufmann/-frau für Spedition und Logistikdienstleistung"],
    ["Kauffrau Tourismus und Freizeit München", "Kaufmann/-frau für Tourismus und Freizeit"],
  ];

  Logger.log("\n[TEST MAPPING CV]");
  tests.forEach(([intitule, role]) => {
    const specialite = detecterSpecialite(intitule, role);
    const filename   = CONFIG.CV_MAPPING[specialite];
    const titre      = getTitreAusbildung(specialite);
    Logger.log(`  "${intitule.substring(0, 50)}"  →  [${specialite}] → "${filename}"`);
    Logger.log(`    Titre email: "${titre}"`);
  });
}
