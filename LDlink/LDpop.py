#!/usr/bin/env python3
import yaml
import json
import math
import os
from pymongo import MongoClient
from bson import json_util, ObjectId
import boto3
import botocore
import subprocess
import sys
import time
from LDcommon import checkS3File, retrieveAWSCredentials, genome_build_vars, get_rsnum,connectMongoDBReadOnly
from LDcommon import replace_coord_rsid,validsnp,get_coords,get_coords,get_query_variant_c
from LDutilites import get_config

# Create LDpop function
def calculate_pop(snp1, snp2, pop, r2_d, web, genome_build, request=None):
    # trim any whitespace
    snp1 = snp1.lower().strip()
    snp2 = snp2.lower().strip() 

    snp1_input = snp1
    snp2_input = snp2

    # Set data directories using config.yml
    param_list = get_config()
    dbsnp_version = param_list['dbsnp_version']
    population_samples_dir = param_list['population_samples_dir']
    data_dir = param_list['data_dir']
    tmp_dir = param_list['tmp_dir']
    genotypes_dir = param_list['genotypes_dir']
    aws_info = param_list['aws_info']

    export_s3_keys = retrieveAWSCredentials()

    # Ensure tmp directory exists
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir)

    # Create JSON output
    output = {}

    # Validate genome build param
    #if return value is string, then it is error message and need to return the message
    snps = validsnp(None,genome_build,None)
    if isinstance(snps, str):
       return snps
   
    # Connect to Mongo snp database
    db = connectMongoDBReadOnly(web)

    snp1 = replace_coord_rsid(db, snp1,genome_build,output)
    snp2 = replace_coord_rsid(db, snp2,genome_build,output)

    snp1_ldpair = snp1
    snp2_ldpair = snp2
    
    snp1_coord = get_coords(db, snp1)
    snp2_coord = get_coords(db, snp2)

    # Check if RS numbers are in snp database
    # SNP1
    if snp1_coord == None or snp1_coord[genome_build_vars[genome_build]['position']] == "NA":
        output["error"] = snp1 + " is not in dbSNP build " + dbsnp_version + " (" + genome_build_vars[genome_build]['title'] + ")."
        #if web:
        output = json.dumps(output, sort_keys=True, indent=2)
        return output
    # SNP2
    if snp2_coord == None or snp2_coord[genome_build_vars[genome_build]['position']] == "NA":
        output["error"] = snp2 + " is not in dbSNP build " + dbsnp_version + " (" + genome_build_vars[genome_build]['title'] + ")."
        #if web:
        output = json.dumps(output, sort_keys=True, indent=2)
        return output
    # Check if SNPs are on the same chromosome
    if snp1_coord['chromosome'] != snp2_coord['chromosome']:
        output["warning"] = str(output["warning"] if "warning" in output else "") + snp1 + " and " + snp2 + " are on different chromosomes. "
 
    # Check if input SNPs are on chromosome Y while genome build == grch38
    # SNP1
    if snp1_coord['chromosome'] == "Y" and (genome_build == "grch38" or genome_build == "grch38_high_coverage"):
        output["error"] = "Input variants on chromosome Y are unavailable for GRCh38, only available for GRCh37 (" + "rs" + snp1_coord['id'] + " - chr" + snp1_coord['chromosome'] + ":" + snp1_coord[genome_build_vars[genome_build]['position']] + ")"
        return(json.dumps(output, sort_keys=True, indent=2))

    # SNP2
    if snp2_coord['chromosome'] == "Y" and (genome_build == "grch38" or genome_build == "grch38_high_coverage"):
        output["error"] = "Input variants on chromosome Y are unavailable for GRCh38, only available for GRCh37 (" + "rs" + snp2_coord['id'] + " - chr" + snp2_coord['chromosome'] + ":" + snp2_coord[genome_build_vars[genome_build]['position']] + ")"
        return(json.dumps(output, sort_keys=True, indent=2))

    # create indexes for population order
    pop_order = {
        "ALL": 1,
        "AFR": 2,
        "YRI": 3,
        "LWK": 4,
        "GWD": 5,
        "MSL": 6,
        "ESN": 7,
        "ASW": 8,
        "ACB": 9,
        "AMR": 10,
        "MXL": 11,
        "PUR": 12,
        "CLM": 13,
        "PEL": 14,
        "EAS": 15,
        "CHB": 16,
        "JPT": 17,
        "CHS": 18,
        "CDX": 19,
        "KHV": 20,
        "EUR": 21,
        "CEU": 22,
        "TSI": 23,
        "FIN": 24,
        "GBR": 25,
        "IBS": 26,
        "SAS": 27,
        "GIH": 28,
        "PJL": 29,
        "BEB": 30,
        "STU": 31,
        "ITU": 32
    }

    pop_groups = {
        "ALL": ["ACB", "ASW", "BEB", "CDX", "CEU", "CHB", "CHS", "CLM", "ESN", "FIN", "GBR", "GIH", "GWD", "IBS", "ITU", "JPT", "KHV", "LWK", "MSL", "MXL", "PEL", "PJL", "PUR", "STU", "TSI", "YRI"],
        "AFR": ["YRI", "LWK", "GWD", "MSL", "ESN", "ASW", "ACB"],
        "AMR": ["MXL", "PUR", "CLM", "PEL"],
        "EAS": ["CHB", "JPT", "CHS", "CDX", "KHV"],
        "EUR": ["CEU", "TSI", "FIN", "GBR" , "IBS"],
        "SAS": ["GIH", "PJL", "BEB", "STU" , "ITU"]
    }

    # empty list for paths to population data
    pop_dirs = []
    pop_split = pop.split("+")
    
    # display superpopulation and all subpopulations
    if "ALL" in pop_split:
        # pop_split.remove("ALL")
        pop_split = pop_split + pop_groups["ALL"] + list(pop_groups.keys())
        pop_split = list(set(pop_split)) # unique elements
    else:
        if "AFR" in pop_split:
            # pop_split.remove("AFR")
            pop_split = pop_split + pop_groups["AFR"]
            pop_split = list(set(pop_split)) # unique elements
        if "AMR" in pop_split:
            # pop_split.remove("AMR")
            pop_split = pop_split + pop_groups["AMR"]
            pop_split = list(set(pop_split)) # unique elements
        if "EAS" in pop_split:
            # pop_split.remove("EAS")
            pop_split = pop_split + pop_groups["EAS"]
            pop_split = list(set(pop_split)) # unique elements
        if "EUR" in pop_split:
            # pop_split.remove("EUR")
            pop_split = pop_split + pop_groups["EUR"]
            pop_split = list(set(pop_split)) # unique elements
        if "SAS" in pop_split:
            # pop_split.remove("SAS")
            pop_split = pop_split + pop_groups["SAS"]
            pop_split = list(set(pop_split)) # unique elements
    
    for pop_i in pop_split:
        if pop_i in ["ALL", "AFR", "AMR", "EAS", "EUR", "SAS", "ACB", "ASW", "BEB", "CDX", "CEU", "CHB", "CHS", "CLM", "ESN", "FIN", "GBR", "GIH", "GWD", "IBS", "ITU", "JPT", "KHV", "LWK", "MSL", "MXL", "PEL", "PJL", "PUR", "STU", "TSI", "YRI"]:
            pop_dirs.append(data_dir + population_samples_dir + pop_i + ".txt")
        else:
            output["error"] = pop_i + " is not an ancestral population. Choose one of the following ancestral populations: AFR, AMR, EAS, EUR, or SAS; or one of the following sub-populations: ACB, ASW, BEB, CDX, CEU, CHB, CHS, CLM, ESN, FIN, GBR, GIH, GWD, IBS, ITU, JPT, KHV, LWK, MSL, MXL, PEL, PJL, PUR, STU, TSI, or YRI."
            #if web:
            output = json.dumps(output, sort_keys=True, indent=2)
            return output
           
    #make empty dictionary to keep sample IDs in for each wanted population 
    ID_dict = {k: [] for k in pop_split}
    adds = ["CHROM", "POS", "ID", "REF", "ALT"]
    
    for pop_i in pop_split:        
        with open(data_dir + population_samples_dir + pop_i + ".txt", "r") as f:
            # print pop_dir + pop_i + ".txt"
            for line in f:
                cleanedLine = line.strip()
                if cleanedLine: # is not empty
                    ID_dict[pop_i].append(cleanedLine)
            for entry in adds:
                ID_dict[pop_i].append(entry)
    
    # Extract 1000 Genomes phased genotypes
    # SNP1
    temp = [snp1, str(snp1_coord['chromosome']), snp1_coord[genome_build_vars[genome_build]['position']]]
    #vcf1,head1 = retrieveTabix1000GDataSingle(temp[2],temp, genome_build, data_dir + genotypes_dir + genome_build_vars[genome_build]['1000G_dir'],request, False)
    (vcf1, head1, output2) = get_query_variant_c(temp, pop_split, str(request), genome_build, False,output)   

    temp = [snp2, str(snp2_coord['chromosome']), snp2_coord[genome_build_vars[genome_build]['position']]]
    (vcf2, head2, output2) = get_query_variant_c(temp, pop_split, str(request), genome_build, False,output)   
    if vcf1 == None or vcf2 == None:
        #if web:
        output = json.dumps(output, sort_keys=True, indent=2)
        return output
    
    rs1_dict = dict(list(zip(head1, vcf1)))
    rs2_dict = dict(list(zip(head2, vcf2)))
    if "<" in rs1_dict["REF"]:
        if "warning" in output:
            output["warning"] = output["warning"] + \
                "." + snp1 + "is a CNV marker. " 
        else:
            output["warning"] = snp1 + "is a CNV marker. " 
            
    if "<" in rs2_dict["REF"]:
        if "warning" in output:
            output["warning"] = output["warning"] + \
                "." + snp2 + "is a CNV marker. " 
        else:
            output["warning"] = snp2 + "is a CNV marker. " 
    
    geno_ind = {
        "rs1" : {k: [] for k in pop_split},
        "rs2" : {k: [] for k in pop_split} 
    }
    
    #SNP1
    for colname in rs1_dict:       
        for key in ID_dict:
            if (colname in ID_dict[key]) and (colname not in adds):
                geno_ind["rs1"][key].append(rs1_dict[colname] + "|." if len(rs1_dict[colname]) == 1 else rs1_dict[colname])
    
    #SNP2            
    for colname in rs2_dict:       
        for key in ID_dict:
            if (colname in ID_dict[key]) and (colname not in adds):
                geno_ind["rs2"][key].append(rs2_dict[colname] + "|." if len(rs2_dict[colname]) == 1 else rs2_dict[colname])
    
    #population freqency dictionary to fill in
    pop_freqs = {
        "ref_freq_snp1" : { }, \
        "ref_freq_snp2" : { }, \
        "alt_freq_snp1" : { }, \
        "alt_freq_snp2" : { }, \
        "total_alleles": { }
    }           
    
    for key in geno_ind["rs1"]:
        pop_freqs["total_alleles"][key] = float(2*geno_ind["rs1"][key].count("0|0") + 2*geno_ind["rs1"][key].count("0|1") +  2*geno_ind["rs1"][key].count("1|1") + 2* geno_ind["rs1"][key].count("1|0") + 2* geno_ind["rs1"][key].count("0|.") + 2* geno_ind["rs1"][key].count("1|."))
        if (pop_freqs["total_alleles"][key] > 0):
            pop_freqs["ref_freq_snp1"][key] = round(((2*geno_ind["rs1"][key].count("0|0") + geno_ind["rs1"][key].count("0|1") + geno_ind["rs1"][key].count("1|0") + geno_ind["rs1"][key].count("1|.") + geno_ind["rs1"][key].count("0|."))/ float(pop_freqs["total_alleles"][key])) *100, 2)
            pop_freqs["ref_freq_snp2"][key] = round(((2*geno_ind["rs2"][key].count("0|0") + geno_ind["rs2"][key].count("0|1") + geno_ind["rs2"][key].count("1|0") + geno_ind["rs2"][key].count("1|.") + geno_ind["rs2"][key].count("0|."))/ float(pop_freqs["total_alleles"][key])) *100, 2)
            pop_freqs["alt_freq_snp1"][key] = round(((2*geno_ind["rs1"][key].count("1|1") + geno_ind["rs1"][key].count("0|1") + geno_ind["rs1"][key].count("1|0") + geno_ind["rs1"][key].count("1|.") + geno_ind["rs1"][key].count("0|."))/ float(pop_freqs["total_alleles"][key])) *100, 2)
            pop_freqs["alt_freq_snp2"][key] = round(((2*geno_ind["rs2"][key].count("1|1") + geno_ind["rs2"][key].count("0|1") + geno_ind["rs2"][key].count("1|0") + geno_ind["rs2"][key].count("1|.") + geno_ind["rs2"][key].count("0|."))/ float(pop_freqs["total_alleles"][key])) *100, 2)
        else :
            output["error"] = "Insufficient haplotype data for " + snp1 + " and " + snp2 + " in 1000G reference panel."
            #if web:
            output = json.dumps(output, sort_keys=True, indent=2)
            return output
        
    #get sample size for each population
    sample_size_dict = {}  
     
    for key in ID_dict:
        sample_size_dict[key] = len(ID_dict[key])- len(adds)
        
    # Combine phased genotype
    # Extract haplotypes
    hap = {k: {"0_0": 0, "0_1": 0, "1_0": 0, "1_1": 0, "0_.": 0, "1_.": 0, "._.": 0, "._0": 0, "._1": 0} for k in pop_split}
    
    for pop in geno_ind["rs1"]:
        if len(geno_ind["rs1"][pop]) == len(geno_ind["rs2"][pop]):
            geno_ind_range = len(geno_ind["rs1"][pop])
        elif len(geno_ind["rs1"][pop]) < len(geno_ind["rs2"][pop]):
            geno_ind_range = len(geno_ind["rs1"][pop])
        else:
            geno_ind_range = len(geno_ind["rs2"][pop])
        for ind in range(geno_ind_range):
            # if len(geno_ind["rs1"][pop][ind]) == 3:
            hap1 = geno_ind["rs1"][pop][ind][0] + "_" + geno_ind["rs2"][pop][ind][0]
            hap2 = geno_ind["rs1"][pop][ind][2] + "_" + geno_ind["rs2"][pop][ind][2]
            if hap1 in hap[pop]:
                hap[pop][hap1] += 1           
                hap[pop][hap2] += 1

    # Remove missing haplotypes
    pops = list(hap.keys())
    for pop in pops:
        keys = list(hap[pop].keys())
        for key in keys:
            if "." in key:
                hap[pop].pop(key, None)
        
    # Sort haplotypes
    matrix_values = {k : {"A": "", "B": "", "C": "", "D": "", "N": "", "delta" : "", "Ms" : "" , "D_prime":"", "r2":""} for k in pop_split}
    for pop in hap:
        matrix_values[pop]["A"] = hap[pop][sorted(hap[pop])[0]]
        matrix_values[pop]["B"] = hap[pop][sorted(hap[pop])[1]]
        matrix_values[pop]["C"] = hap[pop][sorted(hap[pop])[2]]
        matrix_values[pop]["D"] = hap[pop][sorted(hap[pop])[3]]
        matrix_values[pop]["N"] = matrix_values[pop]["A"] + matrix_values[pop]["B"] + matrix_values[pop]["C"] + matrix_values[pop]["D"]
        matrix_values[pop]["delta"] = float(matrix_values[pop]["A"] * matrix_values[pop]["D"] - matrix_values[pop]["B"] * matrix_values[pop]["C"])
        matrix_values[pop]["Ms"] = float((matrix_values[pop]["A"] + matrix_values[pop]["C"]) * (matrix_values[pop]["B"] + matrix_values[pop]["D"]) * (matrix_values[pop]["A"] + matrix_values[pop]["B"]) * (matrix_values[pop]["C"] + matrix_values[pop]["D"]))
        if matrix_values[pop]["Ms"] != 0:
            # D prime
            if matrix_values[pop]["delta"] < 0:
                matrix_values[pop]["D_prime"] = abs(matrix_values[pop]["delta"] / min((matrix_values[pop]["A"] + matrix_values[pop]["C"]) * (matrix_values[pop]["A"] + matrix_values[pop]["B"]), (matrix_values[pop]["B"] + matrix_values[pop]["D"]) * (matrix_values[pop]["C"] + matrix_values[pop]["D"])))
            else:
                matrix_values[pop]["D_prime"] = abs(matrix_values[pop]["delta"] / min((matrix_values[pop]["A"] + matrix_values[pop]["C"]) * (matrix_values[pop]["C"] + matrix_values[pop]["D"]), (matrix_values[pop]["A"] + matrix_values[pop]["B"]) * (matrix_values[pop]["B"] + matrix_values[pop]["D"])))
            # R2
            matrix_values[pop]["r2"]= (matrix_values[pop]["delta"]**2) / matrix_values[pop]["Ms"]
            num = (matrix_values[pop]["A"] + matrix_values[pop]["B"] + matrix_values[pop]["C"] + matrix_values[pop]["D"]) * (matrix_values[pop]["A"] * matrix_values[pop]["D"] - matrix_values[pop]["B"] * matrix_values[pop]["C"])**2
            denom = matrix_values[pop]["Ms"]
            matrix_values[pop]["chisq"] = num / denom
            matrix_values[pop]["p"] = 2 * (1 - (0.5 * (1 + math.erf(matrix_values[pop]["chisq"] **0.5 / 2**0.5))))
        else:
            matrix_values[pop]["D_prime"] = "NA"
            matrix_values[pop]["r2"] = "NA"
            matrix_values[pop]["chisq"] = "NA"
            matrix_values[pop]["p"] = "NA"
    
    for pops in sample_size_dict:    
        output[pops] = {
            'Population': pops , 
            'N': sample_size_dict[pops], \
            # rs1_dict["ID"] + ' Allele Freq': {
            #     rs1_dict["REF"] : str(pop_freqs["ref_freq_snp1"][pops]) + "%", \
            #     rs1_dict["ALT"] : str(pop_freqs["alt_freq_snp1"][pops]) + "%"
            # }, \
            # rs2_dict["ID"] + ' Allele Freq': {
            #     rs2_dict["REF"] : str(pop_freqs["ref_freq_snp2"][pops]) + "%", \
            #     rs2_dict["ALT"] : str(pop_freqs["alt_freq_snp2"][pops]) + "%"
            # }, 
            'rs#1 Allele Freq': {
                rs1_dict["REF"] : str(pop_freqs["ref_freq_snp1"][pops]) + "%", \
                rs1_dict["ALT"] : str(pop_freqs["alt_freq_snp1"][pops]) + "%"
            }, \
            'rs#2 Allele Freq': {
                rs2_dict["REF"] : str(pop_freqs["ref_freq_snp2"][pops]) + "%", \
                rs2_dict["ALT"] : str(pop_freqs["alt_freq_snp2"][pops]) + "%"
            }, 
            "D'" : matrix_values[pops]["D_prime"] if isinstance(matrix_values[pops]["D_prime"], str) else round(float(matrix_values[pops]["D_prime"]), 4), \
            "R2" : matrix_values[pops]["r2"] if isinstance(matrix_values[pops]["r2"], str) else round(float(matrix_values[pops]["r2"]), 4), \
            "chisq" : matrix_values[pops]["chisq"] if isinstance(matrix_values[pops]["chisq"], str) else round(float(matrix_values[pops]["chisq"]), 4), \
            "p" : matrix_values[pops]["p"] if isinstance(matrix_values[pops]["p"], str) else round(float(matrix_values[pops]["p"]), 4)
        }
    
    # print json.dumps(output)

    location_data = {
        "ALL" : {
            "location": "All Populations"
        },
        "AFR" : {
            "location": "African"
        },
        "AMR" : {
            "location": "Ad Mixed American"
        },
        "EAS" : {
            "location": "East Asian"
        },
        "EUR" : {
            "location": "European"
        },
        "SAS" : {
            "location": "South Asian"
        },
        "YRI": {
            "location": "Yoruba in Ibadan, Nigeria",
            "superpopulation": "AFR",
            "latitude": 7.40026,
            "longitude": 3.910742
        },
        "LWK": {
            "location": "Luhya in Webuye, Kenya",
            "superpopulation": "AFR",
            "latitude": 0.59738,
            "longitude": 34.777227
        },
        "GWD": {
            "location": "Gambian in Western Divisions in the Gambia",
            "superpopulation": "AFR",
            "latitude": 13.474133,
            "longitude": -16.394272
        },
        "MSL": {
            "location": "Mende in Sierra Leone",
            "superpopulation": "AFR",
            "latitude": 8.176076,
            "longitude": -11.040253
        },
        "ESN": {
            "location": "Esan in Nigeria",
            "superpopulation": "AFR",
            "latitude": 6.687988,
            "longitude": 6.212868
        },
        "ASW": {
            "location": "Americans of African Ancestry in SW USA",
            "superpopulation": "AFR",
            "latitude": 35.310647,
            "longitude": -107.975885
        },
        "ACB": {
            "location": "African Caribbeans in Barbados",
            "superpopulation": "AFR",
            "latitude": 13.172483,
            "longitude": -59.552779
        },
        "MXL": {
            "location": "Mexican Ancestry from Los Angeles USA",
            "superpopulation": "AMR",
            "latitude": 34.113837,
            "longitude": -118.440427
        },
        "PUR": {
            "location": "Puerto Ricans from Puerto Rico",
            "superpopulation": "AMR",
            "latitude": 18.234429,
            "longitude": -66.418775
        },
        "CLM": {
            "location": "Colombians from Medellin, Colombia",
            "superpopulation": "AMR",
            "latitude": 6.252089,
            "longitude": -75.594652
        },
        "PEL": {
            "location": "Peruvians from Lima, Peru",
            "superpopulation": "AMR",
            "latitude": -12.046543,
            "longitude": -77.046155
        },
        "CHB": {
            "location": "Han Chinese in Beijing, China",
            "superpopulation": "EAS",
            "latitude": 39.906802,
            "longitude": 116.407323
        },
        "JPT": {
            "location": "Japanese in Tokyo, Japan",
            "superpopulation": "EAS",
            "latitude": 35.709444,
            "longitude": 139.731815
        },
        "CHS": {
            "location": "Southern Han Chinese",
            "superpopulation": "EAS",
            "latitude": 24.719998,
            "longitude": 113.043464
        },
        "CDX": {
            "location": "Chinese Dai in Xishuangbanna, China",
            "superpopulation": "EAS",
            "latitude": 22.008264,
            "longitude": 100.796045
        },
        "KHV": {
            "location": "Kinh in Ho Chi Minh City, Vietnam",
            "superpopulation": "EAS",
            "latitude": 10.812236,
            "longitude": 106.633978
        },
        "CEU": {
            "location": "Utah Residents (CEPH) with Northern and Western European Ancestry",
            "superpopulation": "EUR",
            "latitude": 39.250493,
            "longitude": -111.631295
        },
        "TSI": {
            "location": "Toscani in Italia",
            "superpopulation": "EUR",
            "latitude": 43.444187,
            "longitude": 11.117199
        },
        "FIN": {
            "location": "Finnish in Finland",
            "superpopulation": "EUR",
            "latitude": 63.112,
            "longitude": 26.770837
        },
        "GBR": {
            "location": "British in England and Scotland",
            "superpopulation": "EUR",
            "latitude": 54.55902,
            "longitude": -2.143222
        },
        "IBS": {
            "location": "Iberian Population in Spain",
            "superpopulation": "EUR",
            "latitude": 40.482057,
            "longitude": -4.088383
        },
        "GIH": {
            "location": "Gujarati Indian from Houston, Texas",
            "superpopulation": "SAS",
            "latitude": 29.760619,
            "longitude": -95.361356
        },
        "PJL": {
            "location": "Punjabi from Lahore, Pakistan",
            "superpopulation": "SAS",
            "latitude": 31.515188,
            "longitude": 74.357703
        },
        "BEB": {
            "location": "Bengali from Bangladesh",
            "superpopulation": "SAS",
            "latitude": 24.013458,
            "longitude": 90.233561
        },
        "STU": {
            "location": "Sri Lankan Tamil from the UK",
            "superpopulation": "SAS",
            "latitude": 7.595905,
            "longitude": 80.843382
        },
        "ITU": {
            "location": "Indian Telugu from the UK",
            "superpopulation": "SAS",
            "latitude": 15.489823,
            "longitude": 78.487081
        }
    }

    # Change manipulate output data for frontend only if accessed via Web instance
    # if web:
    output_table = { 
        "inputs": {
            "rs1": snp1_input,
            "rs2": snp2_input,
            "LD": r2_d
        },
        "aaData": [],
        "locations": {
            "rs1_rs2_LD_map": [],
            "rs1_map": [],
            "rs2_map": []
        }
    }
    table_data = []
    rs1_map_data = []
    rs2_map_data = []
    rs1_rs2_LD_map_data = []
    # print(list(output.keys()))
    # populate table data
    for key in list(output.keys()):
        if key in list(pop_order.keys()):
            # print key, "parse for table"
            key_order = pop_order[key]
            key_pop = output[key]['Population']
            key_N = output[key]['N']
            # key_rs1_allele_freq = ", ".join([allele + ": " + output[key]['rs#1 Allele Freq'][allele] + "%" for allele in output[key]['rs#1 Allele Freq']])
            key_rs1_allele_freq = rs1_dict["REF"] + ": " + output[key]['rs#1 Allele Freq'][rs1_dict["REF"]] + ", " + rs1_dict["ALT"] + ": " + output[key]['rs#1 Allele Freq'][rs1_dict["ALT"]]
            # key_rs2_allele_freq = ", ".join([allele + ": " + output[key]['rs#2 Allele Freq'][allele] + "%" for allele in output[key]['rs#2 Allele Freq']])
            key_rs2_allele_freq = rs2_dict["REF"] + ": " + output[key]['rs#2 Allele Freq'][rs2_dict["REF"]] + ", " + rs2_dict["ALT"] + ": " + output[key]['rs#2 Allele Freq'][rs2_dict["ALT"]]
            key_D_prime = output[key]["D'"]
            key_R_2 = output[key]['R2']
            # set up data for ldpair link
            ldpair_pops = [key]
            key_chisq = output[key]['chisq']
            key_p = output[key]['p']
            if key in list(pop_groups.keys()):
                ldpair_pops = pop_groups[key]
            ldpair_data = [snp1_ldpair, snp2_ldpair, "%2B".join(ldpair_pops)]
            table_data.append([key_order, key_pop, key_N, key_rs1_allele_freq, key_rs2_allele_freq, key_R_2, key_D_prime, ldpair_data, key_chisq, key_p])
            # populate map data
            if key not in list(pop_groups.keys()):
                rs1_rs2_LD_map_data.append([key, location_data[key]["location"], location_data[key]["superpopulation"], location_data[key]["latitude"], location_data[key]["longitude"], key_rs1_allele_freq, key_rs2_allele_freq, key_R_2, key_D_prime])
                rs1_map_data.append([key, location_data[key]["location"], location_data[key]["superpopulation"], location_data[key]["latitude"], location_data[key]["longitude"], key_rs1_allele_freq])
                rs2_map_data.append([key, location_data[key]["location"], location_data[key]["superpopulation"], location_data[key]["latitude"], location_data[key]["longitude"], key_rs2_allele_freq])
    # Add map data
    output_table["locations"]["rs1_rs2_LD_map"] = rs1_rs2_LD_map_data
    output_table["locations"]["rs1_map"] = rs1_map_data
    output_table["locations"]["rs2_map"] = rs2_map_data
    def getKeyOrder(element):
        return element[0]
    table_data.sort(key=getKeyOrder)
    # Add table data sorting order of rows
    output_table["aaData"] = [xs[1:] for xs in table_data]
    # Add final row link to LDpair
    # ldpair_pops = []
    # for pop in output.keys():
    #     if pop not in pop_groups.keys() and len(pop) == 3:
    #         ldpair_pops.append(pop)
    # ldpair_data = [snp1_input, snp2_input, "%2B".join(ldpair_pops)]
    # output_table["aaData"].append(["LDpair", ldpair_data, ldpair_data, ldpair_data, ldpair_data, ldpair_data])
    if "warning" in output:
        output_table["warning"] = output["warning"]
    if "error" in output:
        output_table["error"] = output["error"]
    # Generate output file
    with open(tmp_dir + "LDpop_" + request + ".txt", "w") as ldpop_out:
        ldpop_out.write("\t".join(["Population", "Abbrev", "N", output_table["inputs"]["rs1"] + " Allele Freq", output_table["inputs"]["rs2"] + " Allele Freq", "R2", "D\'", "Chisq", "P"]) + "\n")
        # print("output_table", output_table)
        # print('output_table["aaData"]', output_table["aaData"])
        for row in output_table["aaData"]:
            ldpop_out.write(str(location_data[row[0]]["location"] + "\t" + row[0]) + "\t" + str(row[1]) + "\t" + str(row[2]) + "\t" + str(row[3]) + "\t" + str(row[4]) + "\t" + str(row[5]) + "\t" + str(row[7]) + "\t" + str(row[8]) + "\n")
        if "error" in output_table:
            ldpop_out.write("\n")
            ldpop_out.write(output_table["error"])
        if "warning" in output_table:
            ldpop_out.write("\n")
            ldpop_out.write(output_table["warning"])

    # Change manipulate output data for frontend only if accessed via Web instance
    # if web:
    output = json.dumps(output_table, sort_keys=True, indent=2)
        
    return output

def main():
    snp1 = sys.argv[1]
    snp2 = sys.argv[2]
    pop = sys.argv[3]
    r2_d = sys.argv[4]
    genome_build = sys.argv[5]
    web = False
    request = None

    # Run function
    out_json = calculate_pop(snp1, snp2, pop, r2_d, web, genome_build, request)

    # Print output
    # print out_json

if __name__ == "__main__":
    main()
