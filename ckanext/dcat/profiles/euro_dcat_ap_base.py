import json
from decimal import Decimal, DecimalException

from ckan.plugins.toolkit import h


from rdflib import term, URIRef, BNode, Literal
import ckantoolkit as toolkit

from ckan.lib.munge import munge_tag

from ckanext.dcat.utils import (
    resource_uri,
    DCAT_EXPOSE_SUBCATALOGS,
    DCAT_CLEAN_TAGS,
    publisher_uri_organization_fallback,
)
from .base import RDFProfile, URIRefOrLiteral, CleanedURIRef
from .base import (
    RDF,
    XSD,
    SKOS,
    RDFS,
    DCAT,
    DCT,
    ADMS,
    VCARD,
    FOAF,
    SCHEMA,
    LOCN,
    GSP,
    OWL,
    SPDX,
    GEOJSON_IMT,
    namespaces,
)

import logging
log = logging.getLogger(__name__)


config = toolkit.config


DISTRIBUTION_LICENSE_FALLBACK_CONFIG = "ckanext.dcat.resource.inherit.license"


LICENSE_URI = {
    'odc-pddl': 'http://www.opendefinition.org/licenses/odc-pddl',
    'odc-odbl': 'http://www.opendefinition.org/licenses/odc-odbl',
    'odc-by': 'http://www.opendefinition.org/licenses/odc-by',
    'cc-zero': 'http://www.opendefinition.org/licenses/cc-zero',
    'cc-by': 'http://www.opendefinition.org/licenses/cc-by',
    'cc-by-sa': 'http://www.opendefinition.org/licenses/cc-by-sa',
    'gfdl': 'http://www.opendefinition.org/licenses/gfdl',
    'uk-ogl': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
    'cc-nc': 'http://creativecommons.org/licenses/by-nc/2.0/',
}

class BaseEuropeanDCATAPProfile(RDFProfile):
    """
    A base profile with common RDF properties across the different DCAT-AP versions

    """

    def _parse_dataset_base(self, dataset_dict, dataset_ref):

        dataset_dict["extras"] = []
        dataset_dict["resources"] = []

        # Basic fields
        for key, predicate in (
            ("title", DCT.title),
            ("notes", DCT.description),
            ("url", DCAT.landingPage),
            ("version", OWL.versionInfo),
        ):
            value = self._object_value(dataset_ref, predicate)
            if value:
                dataset_dict[key] = value

        if not dataset_dict.get("version"):
            # adms:version was supported on the first version of the DCAT-AP
            value = self._object_value(dataset_ref, ADMS.version)
            if value:
                dataset_dict["version"] = value

        # Tags
        # replace munge_tag to noop if there's no need to clean tags
        do_clean = toolkit.asbool(config.get(DCAT_CLEAN_TAGS, False))
        tags_val = [
            munge_tag(tag) if do_clean else tag for tag in self._keywords(dataset_ref)
        ]
        tags = [{"name": tag} for tag in tags_val]
        dataset_dict["tags"] = tags

        # Extras

        #  Simple values
        for key, predicate in (
            ("issued", DCT.issued),
            ("modified", DCT.modified),
            ("identifier", DCT.identifier),
            ("version_notes", ADMS.versionNotes),
            ("frequency", DCT.accrualPeriodicity),
            ("provenance", DCT.provenance),
            ("dcat_type", DCT.type),
        ):
            value = self._object_value(dataset_ref, predicate)
            if value:
                dataset_dict["extras"].append({"key": key, "value": value})

        #  Lists
        for key, predicate, in (
            ("language", DCT.language),
            ("theme", DCAT.theme),
            ("alternate_identifier", ADMS.identifier),
            ("conforms_to", DCT.conformsTo),
            ("documentation", FOAF.page),
            ("related_resource", DCT.relation),
            ("has_version", DCT.hasVersion),
            ("is_version_of", DCT.isVersionOf),
            ("source", DCT.source),
            ("sample", ADMS.sample),
        ):
            values = self._object_value_list(dataset_ref, predicate)
            if values:
                dataset_dict["extras"].append({"key": key, "value": json.dumps(values)})

        # Contact details
        contact = self._contact_details(dataset_ref, DCAT.contactPoint)
        if not contact:
            # adms:contactPoint was supported on the first version of DCAT-AP
            contact = self._contact_details(dataset_ref, ADMS.contactPoint)

        if contact:
            for key in ("uri", "name", "email", "identifier"):
                if contact.get(key):
                    dataset_dict["extras"].append(
                        {"key": "contact_{0}".format(key), "value": contact.get(key)}
                    )

        # Publisher
        publisher = self._agent_details(dataset_ref, DCT.publisher)
        for key in ("uri", "name", "email", "url", "type", "identifier"):
            if publisher.get(key):
                dataset_dict["extras"].append(
                    {"key": "publisher_{0}".format(key), "value": publisher.get(key)}
                )

        # Creator
        creator = self._agent_details(dataset_ref, DCT.creator)
        for key in ("uri", "name", "email", "url", "type", "identifier"):
            if creator.get(key):
                dataset_dict["extras"].append(
                    {"key": "creator_{0}".format(key), "value": creator.get(key)}
                )
        
        # Temporal
        start, end = self._time_interval(dataset_ref, DCT.temporal)
        if start:
            dataset_dict["extras"].append({"key": "temporal_start", "value": start})
        if end:
            dataset_dict["extras"].append({"key": "temporal_end", "value": end})

        # Spatial
        spatial = self._spatial(dataset_ref, DCT.spatial)
        for key in ("uri", "text", "geom"):
            self._add_spatial_to_dict(dataset_dict, key, spatial)

        # Dataset URI (explicitly show the missing ones)
        dataset_uri = str(dataset_ref) if isinstance(dataset_ref, term.URIRef) else ""
        dataset_dict["extras"].append({"key": "uri", "value": dataset_uri})

        # access_rights
        access_rights = self._access_rights(dataset_ref, DCT.accessRights)
        if access_rights:
            dataset_dict["extras"].append(
                {"key": "access_rights", "value": access_rights}
            )

        # License
        if "license_id" not in dataset_dict:
            dataset_dict["license_id"] = self._license(dataset_ref)

        # Source Catalog
        if toolkit.asbool(config.get(DCAT_EXPOSE_SUBCATALOGS, False)):
            catalog_src = self._get_source_catalog(dataset_ref)
            if catalog_src is not None:
                src_data = self._extract_catalog_dict(catalog_src)
                dataset_dict["extras"].extend(src_data)

        # Resources
        for distribution in self._distributions(dataset_ref):

            resource_dict = {}

            #  Simple values
            for key, predicate in (
                ("name", DCT.title),
                ("description", DCT.description),
                ("access_url", DCAT.accessURL),
                ("download_url", DCAT.downloadURL),
                ("issued", DCT.issued),
                ("modified", DCT.modified),
                ("status", ADMS.status),
                ("license", DCT.license),
            ):
                value = self._object_value(distribution, predicate)
                if value:
                    resource_dict[key] = value

            resource_dict["url"] = self._object_value(
                distribution, DCAT.downloadURL
            ) or self._object_value(distribution, DCAT.accessURL)
            #  Lists
            for key, predicate in (
                ("language", DCT.language),
                ("documentation", FOAF.page),
                ("conforms_to", DCT.conformsTo),
            ):
                values = self._object_value_list(distribution, predicate)
                if values:
                    resource_dict[key] = json.dumps(values)

            # rights
            rights = self._access_rights(distribution, DCT.rights)
            if rights:
                resource_dict["rights"] = rights

            # Format and media type
            normalize_ckan_format = toolkit.asbool(
                config.get("ckanext.dcat.normalize_ckan_format", True)
            )
            imt, label = self._distribution_format(distribution, normalize_ckan_format)

            if imt:
                resource_dict["mimetype"] = imt

            if label:
                resource_dict["format"] = label
            elif imt:
                resource_dict["format"] = imt

            # Size
            size = self._object_value_int(distribution, DCAT.byteSize)
            if size is not None:
                resource_dict["size"] = size

            # Checksum
            for checksum in self.g.objects(distribution, SPDX.checksum):
                algorithm = self._object_value(checksum, SPDX.algorithm)
                checksum_value = self._object_value(checksum, SPDX.checksumValue)
                if algorithm:
                    resource_dict["hash_algorithm"] = algorithm
                if checksum_value:
                    resource_dict["hash"] = checksum_value

            # Distribution URI (explicitly show the missing ones)
            resource_dict["uri"] = (
                str(distribution) if isinstance(distribution, term.URIRef) else ""
            )

            # Remember the (internal) distribution reference for referencing in
            # further profiles, e.g. for adding more properties
            resource_dict["distribution_ref"] = str(distribution)

            dataset_dict["resources"].append(resource_dict)

        if self.compatibility_mode:
            # Tweak the resulting dict to make it compatible with previous
            # versions of the ckanext-dcat parsers
            for extra in dataset_dict["extras"]:
                if extra["key"] in (
                    "issued",
                    "modified",
                    "publisher_name",
                    "publisher_email",
                ):

                    extra["key"] = "dcat_" + extra["key"]

                if extra["key"] == "language":
                    extra["value"] = ",".join(sorted(json.loads(extra["value"])))

        return dataset_dict

    def _graph_from_dataset_base(self, dataset_dict, dataset_ref):

        g = self.g

        for prefix, namespace in namespaces.items():
            g.bind(prefix, namespace)

        g.add((dataset_ref, RDF.type, DCAT.Dataset))

        # Basic fields
        items = [
            ("title", DCT.title, None, Literal),
            ("notes", DCT.description, None, Literal),
            ("url", DCAT.landingPage, None, URIRef, FOAF.Document),
            ("identifier", DCT.identifier, ["guid", "id"], URIRefOrLiteral),
            ("version", OWL.versionInfo, ["dcat_version"], Literal),
            ("version_notes", ADMS.versionNotes, None, Literal),
            ("frequency", DCT.accrualPeriodicity, None, URIRefOrLiteral, DCT.Frequency),
            #("access_rights", DCT.accessRights, None, URIRefOrLiteral, DCT.AccessRights),
            ("dcat_type", DCT.type, None, URIRefOrLiteral),
            #("provenance", DCT.provenance, None, URIRefOrLiteral, DCT.ProvenanceStatement),
        ]
        self._add_triples_from_dict(dataset_dict, dataset_ref, items)

        # Tags
        for tag in dataset_dict.get("tags", []):
            g.add((dataset_ref, DCAT.keyword, Literal(tag["name"])))

        # Dates
        items = [
            ("issued", DCT.issued, ["metadata_created"], Literal),
            ("modified", DCT.modified, ["metadata_modified"], Literal),
        ]
        self._add_date_triples_from_dict(dataset_dict, dataset_ref, items)

        #  Lists
        items = [
            ("language", DCT.language, None, URIRefOrLiteral, DCT.LinguisticSystem),
            ("theme", DCAT.theme, None, URIRef),
            ("conforms_to", DCT.conformsTo, None, URIRefOrLiteral, DCT.Standard),
            ("documentation", FOAF.page, None, URIRefOrLiteral, FOAF.Document),
            ("related_resource", DCT.relation, None, URIRefOrLiteral, RDFS.Resource),
            ("has_version", DCT.hasVersion, None, URIRefOrLiteral),
            ("is_version_of", DCT.isVersionOf, None, URIRefOrLiteral),
            ("source", DCT.source, None, URIRefOrLiteral),
            ("sample", ADMS.sample, None, URIRefOrLiteral, DCAT.Distribution),
        ]
        self._add_list_triples_from_dict(dataset_dict, dataset_ref, items)

        # Contact details
        if any(
            [
                self._get_dataset_value(dataset_dict, "contact_uri"),
                self._get_dataset_value(dataset_dict, "contact_name"),
                self._get_dataset_value(dataset_dict, "contact_email"),
                self._get_dataset_value(dataset_dict, "maintainer"),
                self._get_dataset_value(dataset_dict, "maintainer_email"),
                self._get_dataset_value(dataset_dict, "author"),
                self._get_dataset_value(dataset_dict, "author_email"),
            ]
        ):

            contact_uri = self._get_dataset_value(dataset_dict, "contact_uri")
            if contact_uri:
                contact_details = CleanedURIRef(contact_uri)
            else:
                contact_details = BNode()

            g.add((contact_details, RDF.type, VCARD.Organization))
            g.add((dataset_ref, DCAT.contactPoint, contact_details))

            self._add_triple_from_dict(
                dataset_dict,
                contact_details,
                VCARD.fn,
                "contact_name",
                ["maintainer", "author"],
                value_modifier=self._replace_contact_name
            )
            # Add mail address as URIRef, and ensure it has a mailto: prefix
            self._add_triple_from_dict(
                dataset_dict,
                contact_details,
                VCARD.hasEmail,
                "contact_email",
                ["maintainer_email", "author_email"],
                _type=URIRef,
                value_modifier=self._add_mailto,
            )

        # Publisher
        publisher_ref = None

        if dataset_dict.get("publisher"):
            # Scheming publisher field: will be handled in a separate profile
            pass
        elif any(
            [
                self._get_dataset_value(dataset_dict, "publisher_uri"),
                self._get_dataset_value(dataset_dict, "publisher_name"),
            ]
        ):
            # Legacy publisher_* extras
            publisher_uri = self._get_dataset_value(dataset_dict, "publisher_uri")
            publisher_name = self._get_dataset_value(dataset_dict, "publisher_name")
            
            publishers = h.get_publisher_from_json(None)
            
            publisher_name = next(
                            (item['label'] for item in publishers if item['value'] == publisher_name),
                            publisher_name  # Default value if no match is found
                        )
            
            if publisher_uri:
                publisher_ref = CleanedURIRef(publisher_uri)
            else:
                # No publisher_uri
                publisher_ref = BNode()
            publisher_details = {
                "name": publisher_name,
                "email": self._get_dataset_value(dataset_dict, "publisher_email"),
                "url": self._get_dataset_value(dataset_dict, "publisher_url"),
                "type": self._get_dataset_value(dataset_dict, "publisher_type"),
                "identifier": self._get_dataset_value(dataset_dict, "publisher_identifier"),
            }
        elif dataset_dict.get("organization"):
            # Fall back to dataset org
            org_id = dataset_dict["organization"]["id"]
            org_dict = None
            if org_id in self._org_cache:
                org_dict = self._org_cache[org_id]
            else:
                try:
                    org_dict = toolkit.get_action("organization_show")(
                        {"ignore_auth": True}, {"id": org_id}
                    )
                    self._org_cache[org_id] = org_dict
                except toolkit.ObjectNotFound:
                    pass
            if org_dict:
                publisher_ref = CleanedURIRef(
                    publisher_uri_organization_fallback(dataset_dict)
                )
                publisher_details = {
                    "name": org_dict.get("title"),
                    "email": org_dict.get("email"),
                    "url": org_dict.get("url"),
                    "type": org_dict.get("dcat_type"),
                    "identifier": org_dict.get("identifier"),
                }
        # Add to graph
        if publisher_ref:
            g.add((publisher_ref, RDF.type, FOAF.Agent))
            g.add((dataset_ref, DCT.publisher, publisher_ref))
            
            email = publisher_details.get("email")
            if email:
                email_uri = URIRef(f"mailto:{email}")
                g.add((publisher_ref, FOAF.mbox, email_uri))
                
            items = [
                ("name", FOAF.name, None, Literal),
                ("url", FOAF.homepage, None, URIRef),
                ("type", DCT.type, None, URIRefOrLiteral),
                ("identifier", DCT.identifier, None, URIRefOrLiteral),
            ]
            self._add_triples_from_dict(publisher_details, publisher_ref, items)

        # Creator
        creator_ref = None

        if dataset_dict.get("creator"):
            # Scheming publisher field: will be handled in a separate profile
            pass
        elif any(
            [
                self._get_dataset_value(dataset_dict, "creator_uri"),
                self._get_dataset_value(dataset_dict, "creator_name"),
            ]
        ):
            # Legacy creator_* extras
            creator_uri = self._get_dataset_value(dataset_dict, "creator_uri")
            creator_name = self._get_dataset_value(dataset_dict, "creator_name")
            
            creators = h.get_producer_from_json(None)
            
            creator_name = next(
                            (item['label'] for item in creators if item['value'] == creator_name),
                            creator_name  # Default value if no match is found
                        )
            
            if creator_uri:
                creator_ref = CleanedURIRef(creator_uri)
            else:
                # No creator_uri
                creator_ref = BNode()

            creator_details = {
                "name": creator_name,
                "email": self._get_dataset_value(dataset_dict, "creator_email"),
                "url": self._get_dataset_value(dataset_dict, "creator_url"),
                "type": self._get_dataset_value(dataset_dict, "creator_type"),
                "identifier": self._get_dataset_value(dataset_dict, "creator_identifier"),
            }

        # Add to graph
        if creator_ref:
            g.add((creator_ref, RDF.type, FOAF.Agent))
            g.add((dataset_ref, DCT.creator, creator_ref))  # Use DCT.creator for creator
            items = [
                ("name", FOAF.name, None, Literal),
                ("email", FOAF.mbox, None, Literal),
                ("url", FOAF.homepage, None, URIRef),
                ("type", DCT.type, None, URIRefOrLiteral),
                ("identifier", DCT.identifier, None, URIRefOrLiteral),
            ]
            self._add_triples_from_dict(creator_details, creator_ref, items)    

        # Temporal
        start = self._get_dataset_value(dataset_dict, "temporal_start")
        end = self._get_dataset_value(dataset_dict, "temporal_end")
        if start or end:
            temporal_extent = BNode()

            g.add((temporal_extent, RDF.type, DCT.PeriodOfTime))
            if start:
                self._add_date_triple(temporal_extent, SCHEMA.startDate, start)
            if end:
                self._add_date_triple(temporal_extent, SCHEMA.endDate, end)
            g.add((dataset_ref, DCT.temporal, temporal_extent))

        # Spatial
        
        # Process spatial URI (if available in `dataset_dict`)
        spatial_uri = self._get_dataset_value(dataset_dict, "spatial_uri")
        if spatial_uri:
            self.g.add((dataset_ref, DCT.spatial, URIRef(spatial_uri)))
        
        spatial_text = self._get_dataset_value(dataset_dict, "spatial_text")
        spatial_geom = self._get_dataset_value(dataset_dict, "spatial")

        if spatial_text or spatial_geom:
            spatial_ref = self._get_or_create_spatial_ref(dataset_dict, dataset_ref)

            if spatial_text:
                g.add((spatial_ref, SKOS.prefLabel, Literal(spatial_text)))           
                       
            # Add spatial geometry (GeoJSON and/or WKT) if available
            if spatial_geom:
                self._add_spatial_value_to_graph(spatial_ref, LOCN.geometry, spatial_geom)
                self._add_spatial_value_to_graph(spatial_ref, DCAT.centroid, spatial_geom)
                self._add_spatial_value_to_graph(spatial_ref, DCAT.bbox, spatial_geom)
         

        # Use fallback license if set in config
        resource_license_fallback = None
        if toolkit.asbool(config.get(DISTRIBUTION_LICENSE_FALLBACK_CONFIG, False)):
            if "license_id" in dataset_dict and isinstance(
                URIRefOrLiteral(dataset_dict["license_id"]), URIRef
            ):
                resource_license_fallback = dataset_dict["license_id"]
            elif "license_url" in dataset_dict and isinstance(
                URIRefOrLiteral(dataset_dict["license_url"]), URIRef
            ):
                resource_license_fallback = dataset_dict["license_url"]

        # Statetements
        self._add_statement_to_graph(
            dataset_dict,
            "access_rights",
            dataset_ref,
            DCT.accessRights,
            DCT.RightsStatement
        )

        self._add_statement_to_graph(
            dataset_dict,
            "provenance",
            dataset_ref,
            DCT.provenance,
            DCT.ProvenanceStatement
        )   

        # Resources
        for resource_dict in dataset_dict.get("resources", []):

            distribution = CleanedURIRef(resource_uri(resource_dict))

            g.add((dataset_ref, DCAT.distribution, distribution))

            g.add((distribution, RDF.type, DCAT.Distribution))

            #  Simple values
            items = [
                ("name", DCT.title, None, Literal),
                ("description", DCT.description, None, Literal),
                ("status", ADMS.status, None, URIRefOrLiteral),
                ("rights", DCT.rights, None, URIRefOrLiteral, DCT.RightsStatement),
                #("license", DCT.license, None, URIRefOrLiteral, DCT.LicenseDocument),
                ("access_url", DCAT.accessURL, None, URIRef, RDFS.Resource),
                ("download_url", DCAT.downloadURL, None, URIRef, RDFS.Resource),
            ]

            self._add_triples_from_dict(resource_dict, distribution, items)

            #  Lists
            items = [
                ("documentation", FOAF.page, None, URIRefOrLiteral, FOAF.Document),
                ("language", DCT.language, None, URIRefOrLiteral, DCT.LinguisticSystem),
                ("conforms_to", DCT.conformsTo, None, URIRefOrLiteral, DCT.Standard),
            ]
            self._add_list_triples_from_dict(resource_dict, distribution, items)

            # Statetements
            self._add_statement_to_graph(
                resource_dict,
                "rights",
                distribution,
                DCT.rights,
                DCT.RightsStatement
            )  

            # Set default license for distribution if needed and available

            if resource_license_fallback and not (distribution, DCT.license, None) in g:
                g.add(
                    (
                        distribution,
                        DCT.license,
                        URIRefOrLiteral(resource_license_fallback),
                    )
                )
            
            licenseUri = None
            licenseVal = resource_dict.get("license")
            
            if licenseVal:
                # Check against the mapping
                if licenseVal in LICENSE_URI:
                    licenseUri = LICENSE_URI[licenseVal]  
            
                # Default to "UnknownIPR" if no valid licenseUri found
                if not licenseUri:
                    licenseUri = "http://purl.org/adms/licencetype/UnknownIPR"
            
                # Directly add the type property to the distribution node
                g.add(
                    (
                        distribution,
                        DCT.license,
                        URIRef(licenseUri)
                    )
                )


            # Format mapping logic inside your method
            mimetype = resource_dict.get("mimetype")
            fmt = resource_dict.get("format")
           
            fmt = None
            
            # IANA media types (either URI or Literal) should be mapped as mediaType.
            # In case format is available and mimetype is not set or identical to format,
            # check which type is appropriate.
            if fmt and (not mimetype or mimetype == fmt):
                if (
                    "iana.org/assignments/media-types" in fmt
                    or not fmt.startswith("http")
                    and "/" in fmt
                ):
                    # Output format value as dcat:mediaType instead of dct:format
                    mimetype = fmt
                    fmt = None
                else:
                    # Use dct:format
                    mimetype = None
            
            # Add mediaType if mimetype is correctly set
            if mimetype:
                mimetype = URIRefOrLiteral(mimetype)
                fmt = mimetype
                g.add((distribution, DCAT.mediaType, mimetype))
                if isinstance(mimetype, URIRef):
                    g.add((mimetype, RDF.type, DCT.MediaType))
            
            # Fallback to dct:format if fmt is still set and not replaced with MIME type
            if fmt:
                fmt = URIRefOrLiteral(fmt)
                g.add((distribution, DCT["format"], fmt))
                if isinstance(fmt, URIRef):
                    g.add((fmt, RDF.type, DCT.MediaTypeOrExtent))

            # URL fallback and old behavior
            url = resource_dict.get("url")
            download_url = resource_dict.get("download_url")
            access_url = resource_dict.get("access_url")
            # Use url as fallback for access_url if access_url is not set and download_url is not equal
            if url and not access_url:
                if (not download_url) or (download_url and url != download_url):
                    self._add_triple_from_dict(
                        resource_dict, distribution, DCAT.accessURL, "url", _type=URIRef
                    )

            # Dates
            items = [
                ("issued", DCT.issued, ["created"], Literal),
                ("modified", DCT.modified, ["metadata_modified"], Literal),
            ]

            self._add_date_triples_from_dict(resource_dict, distribution, items)

            # Numbers
            if resource_dict.get("size"):
                try:
                    g.add(
                        (
                            distribution,
                            DCAT.byteSize,
                            Literal(int(resource_dict["size"]), datatype=XSD.nonNegativeInteger),
                        )
                    )
                except (ValueError, TypeError, DecimalException):
                    g.add((distribution, DCAT.byteSize, Literal(resource_dict["size"])))
            # Checksum
            if resource_dict.get("hash"):
                checksum = BNode()
                g.add((checksum, RDF.type, SPDX.Checksum))
                g.add(
                    (
                        checksum,
                        SPDX.checksumValue,
                        Literal(resource_dict["hash"], datatype=XSD.hexBinary),
                    )
                )

                if resource_dict.get("hash_algorithm"):
                    checksum_algo = URIRefOrLiteral(resource_dict["hash_algorithm"])
                    g.add(
                        (
                            checksum,
                            SPDX.algorithm,
                            checksum_algo,
                        )
                    )
                    if isinstance(checksum_algo, URIRef):
                        g.add((checksum_algo, RDF.type, SPDX.ChecksumAlgorithm))

                g.add((distribution, SPDX.checksum, checksum))

    def _graph_from_catalog_base(self, catalog_dict, catalog_ref):

        g = self.g

        for prefix, namespace in namespaces.items():
            g.bind(prefix, namespace)

        g.add((catalog_ref, RDF.type, DCAT.Catalog))

        # Basic fields
        items = [
            ("title", DCT.title, config.get("ckan.site_title"), Literal),
            (
                "description",
                DCT.description,
                config.get("ckan.site_description"),
                Literal,
            ),
            ("homepage", FOAF.homepage, config.get("ckan.site_url"), URIRef),
            (
                "language",
                DCT.language,
                config.get("dcat_lang", "http://publications.europa.eu/resource/authority/language/SWE"),
                URIRefOrLiteral,
            ),
             (
                "spatial",
                DCT.spatial,
                config.get("ckan.site_spatial"),
                URIRef,
            ), 
             (
                "license",
                DCT.license,
                config.get("ckan.site_license"),
                URIRefOrLiteral,
            ),            
        ]
        for item in items:
            key, predicate, fallback, _type = item
            if catalog_dict:
                value = catalog_dict.get(key, fallback)
            else:
                value = fallback
            if value:
                g.add((catalog_ref, predicate, _type(value)))

        # Dates
        modified = self._last_catalog_modification()
        if modified:
            self._add_date_triple(catalog_ref, DCT.modified, modified)
            
        issued = config.get("ckan.site_issued")
        if issued:
            self._add_date_triple(catalog_ref, DCT.issued, issued)    
            
        # Catalog Publisher
        publisher_uri = config.get("ckan.site_publisher_uri")
               
        if publisher_uri:
            publisher_ref = CleanedURIRef(publisher_uri)
        else:
            # No publisher_uri
            publisher_ref = BNode()
        publisher_details = {
            "name": config.get("ckan.site_publisher_name"),
            "email": config.get("ckan.site_publisher_email"),
            "url": config.get("ckan.site_publisher_url"),
            "type": config.get("ckan.site_publisher_type"),
        }
        
        # Add to graph
        if publisher_ref:
            g.add((publisher_ref, RDF.type, FOAF.Agent))
            g.add((catalog_ref, DCT.publisher, publisher_ref))
            
            email = publisher_details.get("email")
            if email:
                email_uri = URIRef(f"mailto:{email}")
                g.add((publisher_ref, FOAF.mbox, email_uri))
                
            items = [
                ("name", FOAF.name, None, Literal),
                ("url", FOAF.homepage, None, URIRef),
                ("type", DCT.type, None, URIRefOrLiteral),
                #("identifier", DCT.identifier, None, URIRefOrLiteral),
            ]
            self._add_triples_from_dict(publisher_details, publisher_ref, items)    
            
            
