#!/usr/bin/env python3

import yaml
import json
import operator
from bson import json_util
import subprocess
import sys

from LDcommon import validsnp,replace_coords_rsid_list,get_coords,get_population
from LDcommon import set_alleles
from LDutilites import get_config
from LDcommon import checkS3File, connectMongoDBReadOnly, genome_build_vars, retrieveTabix1000GData,parse_vcf,get_1000g_data
from LDcommon import get_vcf_snp_params,check_same_chromosome
# Create LDhap function
def calculate_hap(snplst, pop, request, web, genome_build):
    # Set data directories using config.yml
    param_list = get_config()
    dbsnp_version = param_list['dbsnp_version']
    data_dir = param_list['data_dir']
    tmp_dir = param_list['tmp_dir']
    population_samples_dir = param_list['population_samples_dir']
    genotypes_dir = param_list['genotypes_dir']
    aws_info = param_list['aws_info']

    # Create JSON output
    output = {}
    snps = validsnp(snplst,genome_build,30)
    #if return value is string, then it is error message and need to return the message
    if isinstance(snps, str):
        return snps
        
    # Select desired ancestral populations
    pop_ids = get_population(pop,request,output)
    if isinstance(pop_ids, str):
        return pop_ids

    db = connectMongoDBReadOnly(web)

    snps = replace_coords_rsid_list(db, snps,genome_build,output)


    # print("Input SNPs (replace genomic coords with RSIDs)", str(snps))
    # Find RS numbers and genomic coords in snp database
    rs_nums = []
    snp_pos = []
    snp_coords = []
    warn = []
    tabix_coords = ""
    for snp_i in snps:
        if len(snp_i) > 0:  # Length entire list of snps
            if len(snp_i[0]) > 2:  # Length of each snp in snps
                # Check first two charcters are rs and last charcter of each snp
                if (snp_i[0][0:2] == "rs" or snp_i[0][0:3] == "chr") and snp_i[0][-1].isdigit():
                    snp_coord = get_coords(db, snp_i[0])
                    if snp_coord != None and snp_coord[genome_build_vars[genome_build]['position']] != "NA":
                        # check if variant is on chrY for genome build = GRCh38
                        if snp_coord['chromosome'] == "Y" and (genome_build == "grch38" or genome_build == "grch38_high_coverage"):
                            if "warning" in output:
                                output["warning"] = output["warning"] + \
                                    "Input variants on chromosome Y are unavailable for GRCh38, only available for GRCh37 (" + "rs" + snp_coord['id'] + " = chr" + snp_coord['chromosome'] + ":" + snp_coord[genome_build_vars[genome_build]['position']] + "). "
                            else:
                                output["warning"] = "Input variants on chromosome Y are unavailable for GRCh38, only available for GRCh37 (" + "rs" + snp_coord['id'] + " = chr" + snp_coord['chromosome'] + ":" + snp_coord[genome_build_vars[genome_build]['position']] + "). "
                            warn.append(snp_i[0])
                        else:
                            rs_nums.append(snp_i[0])
                            snp_pos.append(snp_coord[genome_build_vars[genome_build]['position']])
                            temp = [snp_i[0], snp_coord['chromosome'], snp_coord[genome_build_vars[genome_build]['position']]]
                            snp_coords.append(temp)
                    else:
                        warn.append(snp_i[0])
                else:
                    warn.append(snp_i[0])
            else:
                warn.append(snp_i[0])

    if warn != []:
        if "warning" in output:
            output["warning"] = output["warning"] + \
                "The following RS number(s) or coordinate(s) inputs have warnings: " + ", ".join(warn) + ". "
        else:
            output["warning"] = "The following RS number(s) or coordinate(s) inputs have warnings: " + ", ".join(warn) + ". "

    if len(rs_nums) == 0:
        output["error"] = "Input variant list does not contain any valid RS numbers or coordinates. " + str(output["warning"] if "warning" in output else "")
        return(json.dumps(output, sort_keys=True, indent=2))

    # Check SNPs are all on the same chromosome
    check_same_chromosome(snp_coords,output)
    # Check max distance between SNPs
    distance_bp = []
    for i in range(len(snp_coords)):
        distance_bp.append(int(snp_coords[i][2]))
    distance_max = max(distance_bp)-min(distance_bp)
    if distance_max > 1000000:
        if "warning" in output:
            output["warning"] = output["warning"] + \
                "Switch rate errors become more common as distance between query variants increases (Query range = "+str(
                    distance_max)+" bp). "
        else:
            output["warning"] = "Switch rate errors become more common as distance between query variants increases (Query range = "+str(
                distance_max)+" bp). "
 
    vcf,head = get_1000g_data(snp_pos, snp_coords,genome_build, data_dir + genotypes_dir + genome_build_vars[genome_build]['1000G_dir'])

    # Extract haplotypes
    index = []
    for i in range(9, len(head)):
        if head[i] in pop_ids:
            index.append(i)

    hap1 = [[]]
    for i in range(len(index)-1):
        hap1.append([])
    hap2 = [[]]
    for i in range(len(index)-1):
        hap2.append([])

    # parse vcf
    #snp_dict,missing_snp,output = parse_vcf(vcf,snp_coords,output,genome_build,True)
    snp_dict,missing_snp,output = parse_vcf(vcf,snp_coords,output,genome_build,True)
    if "error" in output:
       return(json.dumps(output, sort_keys=True, indent=2))
    # throw error if no data is returned from 1000G
              
    rsnum_lst = []
    allele_lst = []
    pos_lst = []
    
    for s_key in snp_dict:
        # parse snp_key such as chr7:pos_rs4
        snp_keys = s_key.split("_")
        snp_key = snp_keys[0].split(':')[1]
        rs_input = snp_keys[1]
        geno_list = snp_dict[s_key] 
        
        for geno in geno_list:
            #print("geno,",geno)
            geno = geno.strip().split()
            geno[0] = geno[0].lstrip('chr')
            # if 1000G position does not match dbSNP position for variant, use dbSNP position
            if geno[1] != snp_key:
                mismatch_msg = "Genomic position ("+geno[1]+") in 1000G data does not match dbSNP" + \
                        dbsnp_version + " (" + genome_build_vars[genome_build]['title'] + ") search coordinates for query variant " +\
                        rs_input + ". "
                if "warning" in output:
                    output["warning"] = output["warning"] + mismatch_msg
                else:
                    output["warning"] = mismatch_msg
                # throw an error in the event of missing query SNPs in 1000G data
                geno[1] = snp_key
    
            if "," not in geno[3] and "," not in geno[4]:
                a1, a2 = set_alleles(geno[3], geno[4])
                count0 = 0
                count1 = 0
                #print(geno)
                for i in range(len(index)):
                    if geno[index[i]] == "0|0":
                        hap1[i].append(a1)
                        hap2[i].append(a1)
                        count0 += 2
                    elif geno[index[i]] == "0|1":
                        hap1[i].append(a1)
                        hap2[i].append(a2)
                        count0 += 1
                        count1 += 1
                    elif geno[index[i]] == "1|0":
                        hap1[i].append(a2)
                        hap2[i].append(a1)
                        count0 += 1
                        count1 += 1
                    elif geno[index[i]] == "1|1":
                        hap1[i].append(a2)
                        hap2[i].append(a2)
                        count1 += 2
                    elif geno[index[i]] == "0":
                        hap1[i].append(a1)
                        hap2[i].append(".")
                        count0 += 1
                    elif geno[index[i]] == "1":
                        hap1[i].append(a2)
                        hap2[i].append(".")
                        count1 += 1
                    else:
                        hap1[i].append(".")
                        hap2[i].append(".")
                rsnum_lst.append(rs_input)
                position = "chr"+geno[0]+":"+geno[1]
                pos_lst.append(position)
                f0 = round(float(count0)/(count0+count1), 4)
                f1 = round(float(count1)/(count0+count1), 4)
                if f0 >= f1:
                    alleles = a1+"="+str(round(f0, 3))+", " + \
                        a2+"="+str(round(f1, 3))
                else:
                    alleles = a2+"="+str(round(f1, 3))+", " + \
                        a1+"="+str(round(f0, 3))
                allele_lst.append(alleles)

    haps = {}
    for i in range(len(index)):
        h1 = "_".join(hap1[i])
        h2 = "_".join(hap2[i])
        if h1 in haps:
            haps[h1] += 1
        else:
            haps[h1] = 1

        if h2 in haps:
            haps[h2] += 1
        else:
            haps[h2] = 1

    # Remove Missing Haplotypes
    keys = list(haps.keys())
    for key in keys:
        if "." in key:
            haps.pop(key, None)

    # Sort results
    results = []
    for hap in haps:
        temp = [hap, haps[hap]]
        results.append(temp)

    total_haps = sum(haps.values())

    results_sort1 = sorted(results, key=operator.itemgetter(0))
    results_sort2 = sorted(
        results_sort1, key=operator.itemgetter(1), reverse=True)

    # Generate JSON output
    digits = len(str(len(results_sort2)))
    haps_out = {}
    for i in range(len(results_sort2)):
        hap_info = {}
        hap_info["Haplotype"] = results_sort2[i][0]
        hap_info["Count"] = results_sort2[i][1]
        hap_info["Frequency"] = round(float(results_sort2[i][1])/total_haps, 4)
        haps_out["haplotype_"+(digits-len(str(i+1)))*"0"+str(i+1)] = hap_info
    output["haplotypes"] = haps_out

    digits = len(str(len(rsnum_lst)))
    snps_out = {}
    for i in range(len(rsnum_lst)):
        snp_info = {}
        snp_info["RS"] = rsnum_lst[i]
        snp_info["Alleles"] = allele_lst[i]
        snp_info["Coord"] = pos_lst[i]
        snps_out["snp_"+(digits-len(str(i+1)))*"0"+str(i+1)] = snp_info
    output["snps"] = snps_out

    # Create SNP File
    snp_out = open(tmp_dir+"snps_"+request+".txt", "w")
    print("RS_Number\tPosition (" + genome_build_vars[genome_build]['title_hg'] + ")\tAllele Frequency", file=snp_out)
    for k in sorted(output["snps"].keys()):
        rs_k = output["snps"][k]["RS"]
        coord_k = output["snps"][k]["Coord"]
        alleles_k0 = output["snps"][k]["Alleles"].strip(" ").split(",")
        alleles_k1 = alleles_k0[0]+"0"*(7-len(str(alleles_k0[0]))) + \
            ","+alleles_k0[1]+"0"*(8-len(str(alleles_k0[1])))
        temp_k = [rs_k, coord_k, alleles_k1]
        print("\t".join(temp_k), file=snp_out)
    snp_out.close()

    # Create Haplotype File
    hap_out = open(tmp_dir+"haplotypes_"+request+".txt", "w")
    print("Haplotype\tCount\tFrequency", file=hap_out)
    for k in sorted(output["haplotypes"].keys()):
        hap_k = output["haplotypes"][k]["Haplotype"]
        count_k = str(output["haplotypes"][k]["Count"])
        freq_k = str(output["haplotypes"][k]["Frequency"])
        temp_k = [hap_k, count_k, freq_k]
        print("\t".join(temp_k), file=hap_out)
    hap_out.close()

    # Return JSON output
    return(json.dumps(output, sort_keys=True, indent=2))

def main():
    # Import LDLink options
    if len(sys.argv) == 6:
        snplst = sys.argv[1]
        pop = sys.argv[2]
        request = sys.argv[3]
        web = sys.argv[4]
        genome_build = sys.argv[5]
    else:
        print("Correct useage is: LDLink.py snplst populations request false")
        sys.exit()

    # Run function
    out_json = calculate_hap(snplst, pop, request, web, genome_build)

    # Print output
    json_dict = json.loads(out_json)

    try:
        json_dict["error"]

    except KeyError:
        hap_lst = []
        hap_count = []
        hap_freq = []
        for k in sorted(json_dict["haplotypes"].keys()):
            hap_lst.append(json_dict["haplotypes"][k]["Haplotype"])
            hap_count.append(
                " "*(6-len(str(json_dict["haplotypes"][k]["Count"])))+str(json_dict["haplotypes"][k]["Count"]))
            hap_freq.append(str(json_dict["haplotypes"][k]["Frequency"])+"0"*(
                6-len(str(json_dict["haplotypes"][k]["Frequency"]))))

        # Only print haplotypes >1 percent frequency
        freq_count = 0
        for i in range(len(hap_freq)):
            if float(hap_freq[i]) >= 0.01:
                freq_count += 1

        hap_snp = []
        for i in range(len(hap_lst[0].split("_"))):
            temp = []
            for j in range(freq_count):  # use "len(hap_lst)" for all haplotypes
                temp.append(hap_lst[j].split("_")[i])
            hap_snp.append(temp)

        print("")
        print("RS Number     Coordinates      Allele Frequency      Common Haplotypes (>1%)")
        print("")
        counter = 0
        for k in sorted(json_dict["snps"].keys()):
            rs_k = json_dict["snps"][k]["RS"]+" " * \
                (11-len(str(json_dict["snps"][k]["RS"])))
            coord_k = json_dict["snps"][k]["Coord"]+" " * \
                (14-len(str(json_dict["snps"][k]["Coord"])))
            alleles_k0 = json_dict["snps"][k]["Alleles"].strip(" ").split(",")
            alleles_k1 = alleles_k0[0]+"0"*(7-len(str(alleles_k0[0]))) + \
                ","+alleles_k0[1]+"0"*(8-len(str(alleles_k0[1])))
            temp_k = [rs_k, coord_k, alleles_k1,
                      "   "+"      ".join(hap_snp[counter])]
            print("   ".join(temp_k))
            counter += 1

        print("")
        print("                                         Count: " + \
            " ".join(hap_count[0:freq_count]))
        print("                                     Frequency: " + \
            " ".join(hap_freq[0:freq_count]))

        try:
            json_dict["warning"]
        except KeyError:
            print("")
        else:
            print("")
            print("WARNING: "+json_dict["warning"]+"!")
            print("")

    else:
        print("")
        print(json_dict["error"])
        print("")


if __name__ == "__main__":
    main()
